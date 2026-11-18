"""A2-2 — the interception seam: dispatch modes and the post-autocut hook point.

The gate this file exists to prove:

  *A2-2.1* with zero listeners, output byte-identical to baseline
  *A2-2.2* a listener that does NOT call next() removes exactly one result

"Byte-identical" is asserted as object identity, not equality — a rebuilt list
with equal contents would still be a behavioural change (new identity, new
mutation semantics), so `is` is the only honest check.
"""
import unittest

import server.dispatch as dispatch


class TestPassThrough(unittest.TestCase):
    """Zero listeners must be a true no-op."""

    def setUp(self):
        dispatch.clear()

    def tearDown(self):
        dispatch.clear()

    def test_waterfall_returns_the_same_object(self):
        items = [{"id": 1}, {"id": 2}, {"id": 3}]
        self.assertIs(dispatch.waterfall("t.none", items), items)

    def test_emit_and_parallel_and_serial_are_empty(self):
        self.assertIsNone(dispatch.emit("t.none"))
        self.assertEqual(dispatch.parallel("t.none"), [])
        self.assertEqual(dispatch.serial("t.none"), [])
        self.assertIsNone(dispatch.bail("t.none"))

    def test_count_zero(self):
        self.assertEqual(dispatch.count("t.none"), 0)


class TestWaterfallVeto(unittest.TestCase):
    """A2-2.2 — a listener that skips next() vetoes and its return becomes the result."""

    def setUp(self):
        dispatch.clear()

    def tearDown(self):
        dispatch.clear()

    def test_veto_removes_exactly_one(self):
        items = [{"id": i} for i in range(4)]

        def drop_one(value, next_):
            # Deliberately does NOT call next_ -> veto the remaining chain.
            return [x for x in value if x["id"] != 2]

        dispatch.register("t.veto", drop_one)
        out = dispatch.waterfall("t.veto", items)
        self.assertEqual(len(out), len(items) - 1)
        self.assertEqual([x["id"] for x in out], [0, 1, 3])

    def test_veto_with_none_return_leaves_value_unchanged(self):
        items = [1, 2, 3]

        def silent_veto(value, next_):
            return None

        dispatch.register("t.veto", silent_veto)
        self.assertIs(dispatch.waterfall("t.veto", items), items)

    def test_next_passes_a_transformed_value_along(self):
        seen = []

        def first(value, next_):
            return next_(value + [1])

        def second(value, next_):
            seen.append(list(value))
            return next_(value + [2])

        dispatch.register("t.chain", first)
        dispatch.register("t.chain", second)
        out = dispatch.waterfall("t.chain", [0])
        self.assertEqual(out, [0, 1, 2])
        self.assertEqual(seen, [[0, 1]])

    def test_veto_stops_the_chain(self):
        ran = []

        def first(value, next_):
            return "stopped"          # veto, never calls next_

        def second(value, next_):
            ran.append("second")
            return next_(value)

        dispatch.register("t.stop", first)
        dispatch.register("t.stop", second)
        self.assertEqual(dispatch.waterfall("t.stop", "start"), "stopped")
        self.assertEqual(ran, [], "a vetoed chain must not run later listeners")

    def test_declaration_order_is_preserved(self):
        order = []

        def mk(n):
            def fn(value, next_):
                order.append(n)
                return next_(value)
            return fn

        dispatch.register("t.order", mk(1))
        dispatch.register("t.order", mk(2))
        dispatch.register("t.order", mk(3))
        dispatch.waterfall("t.order", None)
        self.assertEqual(order, [1, 2, 3])

    def test_a_broken_listener_does_not_corrupt_the_chain(self):
        def boom(value, next_):
            raise RuntimeError("listener blew up")

        def after(value, next_):
            return next_(value + 1)

        dispatch.register("t.err", boom)
        dispatch.register("t.err", after)
        # The failure is logged and the chain continues untransformed.
        self.assertEqual(dispatch.waterfall("t.err", 0), 1)


class TestOtherModes(unittest.TestCase):
    def setUp(self):
        dispatch.clear()

    def tearDown(self):
        dispatch.clear()

    def test_emit_collects_nothing_and_survives_errors(self):
        got = []
        dispatch.register("t.emit", lambda *a: got.append("a"))
        dispatch.register("t.emit", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
        dispatch.register("t.emit", lambda *a: got.append("b"))
        self.assertIsNone(dispatch.emit("t.emit"))
        self.assertEqual(got, ["a", "b"])

    def test_parallel_returns_results_in_order(self):
        dispatch.register("t.par", lambda x: x * 2, label="double")
        dispatch.register("t.par", lambda x: x * 3, label="triple")
        self.assertEqual(dispatch.parallel("t.par", 2), [4, 6])

    def test_serial_runs_all(self):
        dispatch.register("t.ser", lambda x: x + 1)
        dispatch.register("t.ser", lambda x: x + 10)
        self.assertEqual(dispatch.serial("t.ser", 0), [1, 10])

    def test_bail_stops_at_first_non_none(self):
        ran = []
        dispatch.register("t.bail", lambda: (ran.append(1), None)[1])
        dispatch.register("t.bail", lambda: (ran.append(2), "hit")[1])
        dispatch.register("t.bail", lambda: (ran.append(3), "late")[1])
        self.assertEqual(dispatch.bail("t.bail"), "hit")
        self.assertEqual(ran, [1, 2])

    def test_all_five_modes_declared(self):
        self.assertEqual(
            set(dispatch.modes()),
            {"emit", "parallel", "serial", "bail", "waterfall"},
        )


class TestDisposal(unittest.TestCase):
    def setUp(self):
        dispatch.clear()

    def tearDown(self):
        dispatch.clear()

    def test_unregister_callable_is_idempotent(self):
        off = dispatch.register("t.dispose", lambda v, n: v)
        self.assertEqual(dispatch.count("t.dispose"), 1)
        off()
        off()                                     # running teardown twice is safe
        self.assertEqual(dispatch.count("t.dispose"), 0)

    def test_unregister_by_reference(self):
        fn = lambda v, n: v                                  # noqa: E731
        dispatch.register("t.ref", fn)
        self.assertTrue(dispatch.unregister("t.ref", fn))
        self.assertFalse(dispatch.unregister("t.ref", fn))
        self.assertEqual(dispatch.count("t.ref"), 0)


class TestRouterWiring(unittest.TestCase):
    """The seam is genuinely wired into v5's post-autocut return path."""

    def setUp(self):
        dispatch.clear()

    def tearDown(self):
        dispatch.clear()

    def test_channel_name_agrees_between_v5_and_dispatch(self):
        import server.router_v2.v5 as v5
        self.assertEqual(v5._POST_AUTOCUT, dispatch.POST_AUTOCUT)
        self.assertIs(v5._dispatch_waterfall, dispatch.waterfall)

    def test_a_veto_on_the_channel_removes_one_result(self):
        import server.router_v2.v5 as v5
        merged = [{"resource_id": i} for i in range(4)]

        def drop_highest(value, next_):
            return value[:-1]        # veto: does not call next_

        off = dispatch.register(v5._POST_AUTOCUT, drop_highest)
        try:
            self.assertEqual(len(v5._dispatch_waterfall(v5._POST_AUTOCUT, merged)), 3)
        finally:
            off()
        # Unregistered again -> the seam is a pass-through.
        self.assertIs(v5._dispatch_waterfall(v5._POST_AUTOCUT, merged), merged)


if __name__ == "__main__":
    unittest.main()
