from luxvigil.checks.groups import Groups


def test_links_chain():
    g = Groups()
    g.join("a", "b")
    g.join("b", "c")
    g.join("x", "y")
    groups = sorted(sorted(m) for m in g.members().values())
    assert groups == [["a", "b", "c"], ["x", "y"]]


def test_join_all_and_empty():
    g = Groups()
    g.join_all([])  # used to blow up
    g.join_all(["a", "b", "c"])
    assert g.find("a") == g.find("c")
    assert len(g.members()) == 1