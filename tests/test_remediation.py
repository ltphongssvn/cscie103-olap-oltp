# tests/test_remediation.py
"""A failure is data: a stable code, the facts, and what to do about it.

THE ASYMMETRY THIS FIXES. The policy layer already emits structured violations
with ids and reason codes, because a Rego denial is data by construction. The
Python layer did not: it raised RuntimeError with a sentence. So half this
platform's failures were queryable and half were strings, and which half you got
depended on where the failure happened rather than on how serious it was.

WHY THE GUARD RUNS AT CLASS-DEFINITION TIME, HAVING FIRST BEEN A TEST.

The first version walked ActionableError.__subclasses__() and asserted every
code was present and unique. It failed immediately -- on its own fixtures. Five
tests each defined `class ExampleError(ActionableError)` in their bodies, every
one registered, and the uniqueness check saw five duplicates of one code.

The tempting fix is to filter the fixtures out of the assertion. That is a guard
being taught to ignore the only violations it can see, and it leaves the real
hole open: nothing stops a genuinely duplicated code, because the check runs
only when someone remembers to run the suite.

The 2026 practice is to validate during subclass creation. __init_subclass__
fires as the class statement executes, so a missing code, a malformed code or a
duplicate is a definition-time TypeError -- in the test suite, in a notebook, in
production, in a REPL. The invariant no longer depends on being tested; the
tests below simply demonstrate it.
"""

import pytest

from cscie103_olap_oltp.remediation import (
    ActionableError,
    Finding,
    registered_codes,
)


class ExampleError(ActionableError):
    """DEFINED ONCE, AT MODULE SCOPE, WITH ITS OWN CODE.

    Defining it inside each test body is what broke the first design. A fixture
    now obeys the same uniqueness rule as production code, which is the point:
    a rule its own test fixtures can violate is not a rule.
    """

    code = "ERR_TEST_EXAMPLE"


def test_an_error_carries_a_stable_code() -> None:
    """THE CODE IS THE JOIN KEY. A message can be reworded freely; a code is
    what lets two occurrences six months apart be recognised as the same
    failure."""
    assert ExampleError("broke", remediation="fix").code == "ERR_TEST_EXAMPLE"


def test_an_error_carries_its_remediation() -> None:
    """WHAT TO DO NEXT TRAVELS WITH WHAT WENT WRONG.

    An error that says what broke sends the reader to search; one that says
    what to do next ends the incident.
    """
    error = ExampleError("broke", remediation="run `mise run setup`")
    assert "mise run setup" in error.remediation


def test_an_error_renders_every_part() -> None:
    """Two readers need different halves, so one rendering carries both."""
    text = str(ExampleError("the catalog is missing", remediation="deploy first"))
    assert "ERR_TEST_EXAMPLE" in text
    assert "the catalog is missing" in text
    assert "deploy first" in text


def test_context_is_structured_not_interpolated() -> None:
    """FACTS AS FIELDS, NOT AS A FORMATTED STRING.

    Interpolating a path into a message makes it unqueryable: nothing can ask
    "which paths failed this way" without parsing English back apart.
    """
    error = ExampleError("nope", remediation="fix", path="/srv/data", attempts=3)
    assert error.context == {"path": "/srv/data", "attempts": 3}


def test_an_error_converts_to_a_finding() -> None:
    """THE BRIDGE TO THE REST OF THE PLATFORM.

    A Finding is the same shape a Rego denial produces, so a Python failure and
    a policy violation land in one queryable stream instead of two.
    """
    finding = ExampleError("broke", remediation="fix it", path="/srv/data").as_finding()
    assert isinstance(finding, Finding)
    assert finding.id == "ERR_TEST_EXAMPLE"
    assert finding.message == "broke"
    assert finding.remediation == "fix it"
    assert finding.context == {"path": "/srv/data"}


def test_a_finding_serialises_losslessly() -> None:
    """It is written to artifacts and the ledger, so it must round-trip."""
    finding = Finding(id="ERR_X", message="m", remediation="r", context={"a": 1})
    assert Finding.model_validate_json(finding.model_dump_json()) == finding


def test_a_finding_is_frozen() -> None:
    """AN OBSERVED FACT DOES NOT CHANGE. Mutating a recorded failure is how a
    record stops matching what happened."""
    finding = Finding(id="ERR_X", message="m", remediation="r", context={})
    with pytest.raises(Exception, match="frozen"):
        finding.message = "rewritten"


def test_a_subclass_without_a_code_cannot_be_defined() -> None:
    """THE GUARD, AT DEFINITION TIME.

    Not "the suite reports it later" -- the class statement itself fails, so an
    unclassified error cannot exist even transiently.
    """
    with pytest.raises(TypeError, match="declares no code"):

        class UnclassifiedError(ActionableError):
            pass


def test_a_duplicate_code_cannot_be_defined() -> None:
    """TWO FAILURES SHARING A CODE CANNOT BE TOLD APART, which defeats the only
    thing a code is for. Raising here makes the collision impossible rather
    than merely reportable."""
    with pytest.raises(TypeError, match="already registered"):

        class DuplicateError(ActionableError):
            code = "ERR_TEST_EXAMPLE"


def test_a_malformed_code_cannot_be_defined() -> None:
    """ONE CONVENTION MAKES CODES GREPPABLE ACROSS EVERY OUTPUT THIS REPO
    WRITES, so the convention is enforced where codes are created."""
    with pytest.raises(TypeError, match="ERR_"):

        class MalformedError(ActionableError):
            code = "something_wrong"


def test_an_abstract_intermediate_is_exempt() -> None:
    """A LAYER IN THE HIERARCHY IS NOT A FAILURE MODE.

    Requiring a code from an intermediate base would force a meaningless one to
    be invented, and an invented code pollutes exactly the stream this exists to
    keep clean.
    """

    class IntermediateError(ActionableError, abstract=True):
        pass

    class ConcreteError(IntermediateError):
        code = "ERR_TEST_CONCRETE"

    assert ConcreteError("x", remediation="y").code == "ERR_TEST_CONCRETE"


def test_the_registry_reports_real_codes() -> None:
    """THE REGISTRY IS EXPLICIT, not a walk of __subclasses__.

    __subclasses__ never forgets, so a class defined once in a test lingers for
    the process lifetime -- which is what made the first design unusable.
    """
    codes = registered_codes()
    assert "ERR_TEST_EXAMPLE" in codes
    assert len(codes) == len(set(codes))


def test_the_base_class_refuses_to_be_raised_bare() -> None:
    """The base has no code, so raising it would produce an unclassified
    failure -- the thing this module exists to prevent."""
    with pytest.raises(NotImplementedError, match="subclass"):
        raise ActionableError("bare", remediation="none")


def test_the_registry_is_complete_not_merely_populated() -> None:
    """A CATALOGUE THAT REPORTS A SUBSET IS WORSE THAN NONE.

    These codes live in modules this test never imports. If discovery regresses
    to whatever happened to be loaded, they disappear and the registry still
    looks healthy -- which is exactly how the first version passed while
    omitting ERR_LEDGER_LOCKED.
    """
    codes = set(registered_codes())
    assert "ERR_LEDGER_LOCKED" in codes
    assert "ERR_DATABRICKS_NOT_AUTHENTICATED" in codes
