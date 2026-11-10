#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: [Abhinav Tripathi](github.com/kryochronic)
import pytest

from sg_exchange_mcp.identity import EXCHANGES, make_key


def test_keys():
    assert make_key("siemens", "NSE") == "IN::XNSE::SIEMENS"
    assert make_key("NVDA", "XNAS") == "US::XNAS::NVDA"
    with pytest.raises(ValueError):
        make_key("X", "MOON")
    with pytest.raises(ValueError):
        make_key("A::B", "NSE")


def test_registry_matches_backend_when_installed():
    backend = pytest.importorskip("supplygraph.exchanges")
    ours = {m: (e.short, e.country, e.currency) for m, e in EXCHANGES.items()}
    theirs = {m: (e.short, e.country, e.currency) for m, e in backend.EXCHANGES.items()}
    assert ours == theirs, "keep sg_exchange_mcp/identity.py in step with backend/supplygraph/exchanges.py"
