from census_compass.budget import RequestBudget


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_timeout_none_when_below_minimum():
    c = Clock()
    b = RequestBudget(60, clock=c)
    c.t += 56
    assert b.statement_timeout(5) is None


def test_timeout_floored_remaining():
    c = Clock()
    b = RequestBudget(60, clock=c)
    c.t += 10
    assert b.statement_timeout(5) == 50
    c.t += 0.5
    assert b.statement_timeout(5) == 49
    assert not b.expired()


def test_expired():
    c = Clock()
    b = RequestBudget(60, clock=c)
    c.t += 61
    assert b.expired()
    assert b.remaining() == 0
