# -*- coding: utf-8 -*-

# Copyright (c) 2016-2018 by University of Kassel and Fraunhofer Institute for Energy Economics
# and Energy System Technology (IEE), Kassel. All rights reserved.


import pytest
from numpy import array, allclose, isclose

import pandapower as pp
from pandapower.test.consistency_checks import consistency_checks

try:
    import pplog as logging
except ImportError:
    import logging

logger = logging.getLogger(__name__)


@pytest.fixture
def dcline_net():
    net = pp.create_empty_network()

    b5 = pp.create_bus(net, 380)
    b3 = pp.create_bus(net, 380)
    b2 = pp.create_bus(net, 380)
    b4 = pp.create_bus(net, 380)
    b1 = pp.create_bus(net, 380)

    pp.create_ext_grid(net, b1, 1.02, min_p_kw=0., max_p_kw=1e12)
    pp.create_line(net, b1, b2, 30, "490-AL1/64-ST1A 380.0")
    pp.create_dcline(net, name="dc line", from_bus=b2, to_bus=b3, p_kw=0.2e6, loss_percent=1.0,
                     loss_kw=500, vm_from_pu=1.01, vm_to_pu=1.012, max_p_kw=1e6,
                     in_service=True, index=4)
    pp.create_line(net, b3, b4, 20, "490-AL1/64-ST1A 380.0")

    pp.create_load(net, bus=b4, p_kw=800e3, controllable=False)
    pp.create_line(net, b4, b5, 20, "490-AL1/64-ST1A 380.0")
    pp.create_ext_grid(net, b5, 1.02, min_p_kw=0., max_p_kw=1e12)

    return net


def test_dispatch1(dcline_net):
    net = dcline_net
    pp.create_pwl_cost(net, 0, "ext_grid", [(-1e12, 1e12, 0.1)])
    pp.create_pwl_cost(net, 1, "ext_grid", [(-1e12, 1e12, 0.08)])
    net.bus["max_vm_pu"] = 2
    net.bus["min_vm_pu"] = 0  # needs to be constrained more than default
    net.line["max_loading_percent"] = 1000  # does not converge if unconstrained
    pp.runopp(net)

    consistency_checks(net)
    rel_loss_expect = (net.res_dcline.pl_kw - net.dcline.loss_kw) / \
                      (net.res_dcline.p_from_kw - net.res_dcline.pl_kw) * 100
    assert allclose(rel_loss_expect.values, net.dcline.loss_percent.values, rtol=1e-2)

    p_eg_expect = array([5.00078353e+02,  8.05091476e+05])
    q_eg_expect = array([-7787.55773243,  628.30727889])
    assert allclose(net.res_ext_grid.p_kw.values, p_eg_expect)
    assert allclose(net.res_ext_grid.q_kvar.values, q_eg_expect)

    p_from_expect = array([500.0754071])
    q_from_expect = array([7787.45600524])

    assert allclose(net.res_dcline.p_from_kw.values, p_from_expect)
    assert allclose(net.res_dcline.q_from_kvar.values, q_from_expect)

    p_to_expect = array([-0.0746605])
    q_to_expect = array([-627.12636707])

    assert allclose(net.res_dcline.p_to_kw.values, p_to_expect)
    assert allclose(net.res_dcline.q_to_kvar.values, q_to_expect)


def test_dcline_dispatch2(dcline_net):
    net = dcline_net
    pp.create_poly_cost(net, 0, "ext_grid", cp1_eur_per_kw=.08)
    pp.create_poly_cost(net, 1, "ext_grid", cp1_eur_per_kw=.1)
#    pp.create_poly_cost(net, 0, "ext_grid", array([.08, 0]))
#    pp.create_poly_cost(net, 1, "ext_grid", array([.1, 0]))

    net.bus["max_vm_pu"] = 2
    net.bus["min_vm_pu"] = 0# needs to be constrained more than default
    net.line["max_loading_percent"] = 1000  # does not converge if unconstrained
    pp.runopp(net)
    consistency_checks(net, rtol=1e-3)
    consistency_checks(net, rtol=1e-3)
    rel_loss_expect = (net.res_dcline.pl_kw - net.dcline.loss_kw) / \
                      (net.res_dcline.p_from_kw - net.res_dcline.pl_kw) * 100
    assert allclose(rel_loss_expect.values, net.dcline.loss_percent.values)

    p_eg_expect = array([8.21525358e+05,  5.43498903e-02])
    q_eg_expect = array([-7787.55852923,  -21048.59213887])
    assert allclose(net.res_ext_grid.p_kw.values, p_eg_expect)
    assert allclose(net.res_ext_grid.q_kvar.values, q_eg_expect)

    p_from_expect = array([813573.88366999])
    q_from_expect = array([-26446.0473644])

    assert allclose(net.res_dcline.p_from_kw.values, p_from_expect)
    assert allclose(net.res_dcline.q_from_kvar.values, q_from_expect)

    p_to_expect = array([-805023.64719801])
    q_to_expect = array([-21736.31196315])

    assert allclose(net.res_dcline.p_to_kw.values, p_to_expect)
    assert allclose(net.res_dcline.q_to_kvar.values, q_to_expect)


def test_dcline_dispatch3(dcline_net):
    net = dcline_net
    pp.create_poly_cost(net, 4, "dcline", cp1_eur_per_kw=1.5)
    net.bus["max_vm_pu"] = 1.03 # needs to be constrained more than default
    net.line["max_loading_percent"] = 1000  # does not converge if unconstrained
    pp.runopp(net)
    consistency_checks(net, rtol=1e-3)

    # dc line is not dispatched because of the assigned costs
    assert isclose(net.res_dcline.at[4, "p_to_kw"], 0, atol=1e-3)
    assert all(net.res_ext_grid.p_kw.values > 0)

    # costs for ext_grid at the end of the DC line get double the costs of DC line transfer
    pp.create_poly_cost(net, 1, "ext_grid", cp1_eur_per_kw=2)

    pp.runopp(net)

    #now the total power is supplied through the DC line
    assert (net.res_dcline.at[4, "p_to_kw"]) < 1e3
    assert net.res_ext_grid.p_kw.at[1] < 1
    assert isclose(net.res_cost, net.res_dcline.at[4, "p_from_kw"]*1.5)

if __name__ == "__main__":
    pytest.main([__file__])
