# -*- coding: utf-8 -*-

# Copyright 1996-2015 PSERC. All rights reserved.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.

# Copyright (c) 2016-2018 by University of Kassel and Fraunhofer Institute for Energy Economics
# and Energy System Technology (IEE), Kassel. All rights reserved.

from numpy import zeros, array, concatenate, power, ndarray
import pandas as pd
from pandapower.idx_cost import MODEL, NCOST, COST, PW_LINEAR, POLYNOMIAL
from pandapower.idx_gen import PMIN, PMAX

try:
    import pplog as logging
except ImportError:
    import logging

logger = logging.getLogger(__name__)

def get_gen_index(net, et, element):
    if et == "dcline":
        dc_idx = net.dcline.index.get_loc(element)
        element = len(net.gen.index) - 2*len(net.dcline) + dc_idx*2 + 1
        et = "gen"
    lookup = "%s_controllable"%et if et in ["load", "sgen", "storage"] else et
    if lookup in net._pd2ppc_lookups:
        if len(net._pd2ppc_lookups[lookup]) > element:
            return int(net._pd2ppc_lookups[lookup][int(element)])


def map_costs_to_gen(net, cost):
    gens = array([get_gen_index(net, et, element)
              for et, element in zip(cost.et.values, cost.element.values)])
    cost_is = array([gen is not None for gen in gens])
    cost = cost[cost_is]
    gens = gens[cost_is].astype(int)
    signs = array([-1 if element in ["load", "storage", "dcline"] else 1 for element in cost.et])
    return gens, cost, signs

def _init_gencost(ppci, net):
    is_quadratic = net.poly_cost[["cp2_eur_per_kw2", "cq2_eur_per_kvar2"]].values.any()
    q_costs = net.poly_cost[["cq1_eur_per_kvar", "cq2_eur_per_kvar2"]].values.any() or \
              "q" in net.pwl_cost.power_type.values
    rows = len(ppci["gen"])*2 if q_costs else len(ppci["gen"])
    if len(net.pwl_cost):
        nr_points = {len(p) for p in net.pwl_cost.points.values}
        points = max(nr_points)
        if is_quadratic:
            raise ValueError("Quadratic costs can be mixed with piecewise linear costs")
        columns = COST + (max(points, 2) + 1)*2
    else:
        columns = COST + 3 if is_quadratic else COST + 2
    ppci["gencost"] = zeros((rows, columns), dtype=float)
    return is_quadratic, q_costs


def _fill_gencost_poly(ppci, net, is_quadratic, q_costs):
    gens, cost, signs = map_costs_to_gen(net, net.poly_cost)
    c0 = cost["cp0_eur"].values
    c1 = cost["cp1_eur_per_kw"].values
    signs = array([-1 if element in ["load", "storage", "dcline"] else 1 for element in cost.et])
    if is_quadratic:
        c2 = cost["cp2_eur_per_kw2"]
        ppci["gencost"][gens, NCOST] = 3
        ppci["gencost"][gens, COST] = c2 * 1e6 * signs
        ppci["gencost"][gens, COST + 1] = c1 * 1e3 * signs
        ppci["gencost"][gens, COST + 2] = c0 * signs
    else:
        ppci["gencost"][gens, NCOST] = 2
        ppci["gencost"][gens, COST] = c1 * 1e3 * signs
        ppci["gencost"][gens, COST + 1] = c0 * signs
    if q_costs:
        gens_q = gens + len(ppci["gen"])
        c0 = cost["cq0_eur"].values
        c1 = cost["cq1_eur_per_kvar"].values
        signs = array([-1 if element in ["load", "storage"] else 1 for element in cost.et])
        if is_quadratic:
            c2 = cost["cq2_eur_per_kvar2"]
            ppci["gencost"][gens_q, NCOST] = 3
            ppci["gencost"][gens_q, COST] = c2 * 1e6 * signs
            ppci["gencost"][gens_q, COST + 1] = c1 * 1e3 * signs
            ppci["gencost"][gens_q, COST + 2] = c0 * signs
        else:
            ppci["gencost"][gens_q, NCOST] = 2
            ppci["gencost"][gens_q, COST] = c1 * 1e3 * signs
            ppci["gencost"][gens_q, COST + 1] = c0 * signs

def _fill_gencost_pwl(ppci, net):
    for power_mode, cost in net.pwl_cost.groupby("power_type"):
        gens, cost, signs = map_costs_to_gen(net, cost)
        if power_mode == "q":
            gens += len(ppci["gen"])
        for gen, points, sign in zip(gens, cost.points.values, signs):
            costs = costs_from_areas(points, sign)
            print(costs)
            ppci["gencost"][gen, COST:COST+len(costs)] = costs
            ppci["gencost"][gen, NCOST] = len(costs) / 2

def costs_from_areas(points, sign):
    costs = []
    c0 = 0
    last_upper = None
    for lower, upper, cost in points:
        if last_upper is None:
            costs.append(lower * 1e-3)
            c = c0 + lower * cost * sign
            c0 = c
            costs.append(c)
        if last_upper is not None and last_upper != lower:
            raise ValueError
        last_upper = upper
        costs.append(upper * 1e-3)
        c = c0 + (upper - lower) * cost * sign
        c0 = c
        costs.append(c)
    return costs


def _add_linear_costs_as_pwl_cost(ppci, net):
    gens, cost, signs = map_costs_to_gen(net, net.poly_cost)
    ppci["gencost"][gens, NCOST] = 2
    pmin = ppci["gen"][gens, PMIN]
    pmax = ppci["gen"][gens, PMAX]
    ppci["gencost"][gens, COST] = pmin
    ppci["gencost"][gens, COST + 1] = pmin * cost.cp1_eur_per_kw.values * signs * 1e3
    ppci["gencost"][gens, COST + 2] = pmax
    ppci["gencost"][gens, COST + 3] = pmax * cost.cp1_eur_per_kw.values * signs * 1e3

def _make_objective(ppci, net):
    use_old = False
    if use_old and (len(net.piecewise_linear_cost) or len(net.polynomial_cost)):
        ppci =  _make_objective_old(ppci, net)
        print("using old cost function definition")
        return ppci
    is_quadratic, q_costs = _init_gencost(ppci, net)
    if len(net.pwl_cost):
        ppci["gencost"][:, MODEL] = PW_LINEAR
        ppci["gencost"][:, NCOST] = 2
        ppci["gencost"][:, COST + 2] = 1

        _fill_gencost_pwl(ppci, net)
        if is_quadratic:
            raise ValueError("Piecewise linear costs can not be mixed with quadratic costs")
        elif len(net.poly_cost):
            _add_linear_costs_as_pwl_cost(ppci, net)
    elif len(net.poly_cost):
        ppci["gencost"][:, MODEL] = POLYNOMIAL
        _fill_gencost_poly(ppci, net, is_quadratic, q_costs)
    else:
        logger.warning("no costs are given - overall generated power is minimized")
        ppci["gencost"][:, MODEL] = POLYNOMIAL
        ppci["gencost"][:, NCOST] = 2
        ppci["gencost"][:, COST + 1] = 1
    return ppci

def _make_objective_old(ppci, net):
    """
    Implementaton of objective functions for the OPF

    Limitations:
    - Polynomial reactive power costs can only be quadratic, linear or constant

    INPUT:
        **net** - The pandapower format network
        **ppci** - The "internal" pypower format network for PF calculations

    OUTPUT:
        **ppci** - The "internal" pypower format network for PF calculations
    """
    # Determine duplicated cost data
    all_costs = net.polynomial_cost[['type', 'element', 'element_type']].append(
        net.piecewise_linear_cost[['type', 'element', 'element_type']])
    duplicates = all_costs.loc[all_costs.duplicated()]
    if duplicates.shape[0]:
        raise ValueError("There are elements with multipy costs.\nelement_types: %s\n"
                         "element: %s\ntypes: %s" % (duplicates.element_type.values,
                                                     duplicates.element.values,
                                                     duplicates.type.values))
    # Determine length of gencost array
    ng = len(ppci["gen"])
    if (net.piecewise_linear_cost.type == "q").any() or (net.polynomial_cost.type == "q").any():
        len_gencost = 2 * ng
    else:
        len_gencost = 1 * ng

    # get indices
    eg_idx = net._pd2ppc_lookups["ext_grid"] if "ext_grid" in net._pd2ppc_lookups else None
    gen_idx = net._pd2ppc_lookups["gen"] if "gen" in net._pd2ppc_lookups else None
    sgen_idx = net._pd2ppc_lookups["sgen_controllable"] if "sgen_controllable" in \
        net._pd2ppc_lookups else None
    load_idx = net._pd2ppc_lookups["load_controllable"] if "load_controllable" in \
        net._pd2ppc_lookups else None
    stor_idx = net._pd2ppc_lookups["storage_controllable"] if "storage_controllable" in \
        net._pd2ppc_lookups else None
    dc_gens = net.gen.index[(len(net.gen) - len(net.dcline) * 2):]
    from_gens = net.gen.loc[dc_gens[1::2]]
    if gen_idx is not None:
        dcline_idx = gen_idx[from_gens.index]
    else:
        dcline_idx = None

    # calculate size of gencost array
    if len(net.piecewise_linear_cost):
        n_piece_lin_coefficients = net.piecewise_linear_cost.p.values[0].shape[1] * 2
    else:
        n_piece_lin_coefficients = 0
    if len(net.polynomial_cost):
        n_coefficients = max(n_piece_lin_coefficients,  len(net.polynomial_cost.c.values[0][0]))
        if (n_piece_lin_coefficients > 0) & (n_coefficients % 2):
            # avoid uneven n_coefficient in case of (n_piece_lin_coefficients>0)
            n_coefficients += 1
    else:
        n_coefficients = n_piece_lin_coefficients

    if n_coefficients:
        # initialize array
        ppci["gencost"] = zeros((len_gencost, 4 + n_coefficients), dtype=float)
        ppci["gencost"][:, MODEL:COST] = array([2, 0, 0, n_coefficients])


        if len(net.piecewise_linear_cost):
            for cost_type in ["p", "q"]:
                if (net.piecewise_linear_cost.type == cost_type).any():
                    costs = net.piecewise_linear_cost[net.piecewise_linear_cost.type ==
                                                      cost_type].reset_index(drop=True)

                    if cost_type == "q":
                        shift_idx = ng
                        sign_corr = 1
                    else:
                        shift_idx = 0
                        sign_corr = 1
#                    if l

                    # for element types with costs defined
                    for el in pd.unique(costs.element_type):
                        if el == "gen":
                            idx = gen_idx
                        elif el == "sgen":
                            idx = sgen_idx
                        elif el == "ext_grid":
                            idx = eg_idx
                        elif el == "load":
                            idx = load_idx
                        elif el == "storage":
                            idx = stor_idx
                        elif el == "dcline":
                            idx = dcline_idx

                        # cost data to write into gencost
                        # (only write cost data of controllable and in service elements)
                        if el == "ext_grid" or el == "dcline":
                            el_is = net[el].loc[net[el].in_service & net[el].index.isin(
                                costs.loc[costs.element_type == el].element)].index
                        else:
                            el_is = net[el].loc[net[el].controllable & net[el].in_service &
                                                net[el].index.isin(costs.loc[costs.element_type
                                                                             == el].element)].index

                        p = costs.loc[(costs.element_type == el) & (
                            costs.element.isin(el_is))].p.reset_index(drop=True)
                        f = costs.loc[(costs.element_type == el) & (
                            costs.element.isin(el_is))].f.reset_index(drop=True)

                        if len(p) > 0:
                            p = concatenate(p)
                            f = concatenate(f)
                            # gencost indices
                            elements = idx[el_is] + shift_idx
                            ppci["gencost"][elements, COST:COST+n_piece_lin_coefficients:2] = p
                            # gencost for storages: positive costs in pandapower per definition
                            # --> storage gencosts are similar to sgen gencosts
                            if el in ["load", "dcline", "storage"]:
                                ppci["gencost"][elements, COST+1:COST +
                                                n_piece_lin_coefficients+1:2] = - f * 1e3
                            else:
                                ppci["gencost"][elements, COST+1:COST+n_piece_lin_coefficients +
                                                1:2] = f * 1e3 * sign_corr

                            ppci["gencost"][elements,
                                            NCOST] = n_coefficients / 2
                            ppci["gencost"][elements, MODEL] = 1

        if len(net.polynomial_cost):
            for cost_type in ["p", "q"]:
                if (net.polynomial_cost.type == cost_type).any():
                    costs = net.polynomial_cost[net.polynomial_cost.type == cost_type].reset_index(
                        drop=True)

                    if cost_type == "q":
                        shift_idx = ng
                        sign_corr = 1#-1
                    else:
                        shift_idx = 0
                        sign_corr = 1

                    # for element types with costs defined
                    for el in pd.unique(costs.element_type):
                        if el == "gen":
                            idx = gen_idx
                        if el == "sgen":
                            idx = sgen_idx
                        if el == "ext_grid":
                            idx = eg_idx
                        if el == "load":
                            idx = load_idx
                        if el == "storage":
                            idx = stor_idx
                        if el == "dcline":
                            idx = dcline_idx

                        # cost data to write into gencost
                        # (only write cost data of controllable and in service elements)
                        if el == "ext_grid" or el == "dcline":
                            el_is = net[el].loc[net[el].in_service & net[el].index.isin(
                                costs.loc[costs.element_type == el].element)].index
                        else:
                            el_is = net[el].loc[net[el].controllable & net[el].in_service & net[el].index.isin(
                                costs.loc[costs.element_type == el].element)].index

                        c = costs.loc[(costs.element_type == el) &
                                      (costs.element.isin(el_is))].c.reset_index(drop=True)

                        if len(c) > 0 and isinstance(idx, ndarray):
                            c = concatenate(c)
                            n_c = c.shape[1]
                            c = c * power(1e3, array(range(n_c))[::-1])
                            # gencost indices
                            elements = idx[el_is] + shift_idx
                            n_gencost = ppci["gencost"].shape[1]

                            elcosts = costs[costs.element_type == el]
                            elcosts.index = elcosts.element

                            # gencost for storages: positive costs in pandapower per definition
                            # --> storage gencosts are similar to sgen gencosts
                            if el in ["load", "dcline", "storage"]:
                                ppci["gencost"][elements,  COST:(COST + n_c):] = - c
                            else:
                                ppci["gencost"][elements, -n_c:n_gencost] = c * sign_corr

                            ppci["gencost"][elements, NCOST] = n_coefficients
                            ppci["gencost"][elements, MODEL] = 2

    else:
        ppci["gencost"] = zeros((len_gencost, 8), dtype=float)
        # initialize as pwl cost - otherwise we will get a user warning from
        # pypower for unspecified costs.
        ppci["gencost"][:, :] = array([1, 0, 0, 2, 0, 0, 1, 1000])

    return ppci
