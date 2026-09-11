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
    BARE_BASE_MESSAGE,
    CODE_PATTERN,
    VIOLATION_MESSAGES,
    ActionableError,
    ErrorCodeViolationError,
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


class IntermediateError(ActionableError, abstract=True):
    """A LAYER IN THE HIERARCHY, WHICH IS NOT A FAILURE MODE.

    Requiring a code here would force a meaningless one to be invented, and an
    invented code pollutes exactly the stream this module exists to keep clean.
    """


class ConcreteError(IntermediateError):
    """AT MODULE SCOPE, AND MUTATION TESTING IS WHAT PROVED IT HAD TO BE.

    This pair was defined inside the test body. Defining a class REGISTERS its
    code in a process-global dict, so the statement ran once per test
    invocation -- and mutmut runs the suite once per mutant in a single
    process. The second run collided with the first and the session aborted
    before a single mutant was judged.

    THE FILE ALREADY SAID SO. ExampleError above carries the note that defining
    fixtures inside test bodies is what broke the first design; this one was
    written the way that note forbids.

    SHARED MUTABLE STATE SURFACING AS A FAILURE IS THE USEFUL SIGNAL, and the
    ordinary suite hides it by running exactly once. A test that cannot run
    twice is not isolated, whatever a single green run suggests.
    """

    code = "ERR_TEST_CONCRETE"


def test_an_abstract_intermediate_is_exempt() -> None:
    """The intermediate needs no code; its concrete subclass still does."""
    assert ConcreteError("x", remediation="y").code == "ERR_TEST_CONCRETE"


def test_the_suite_can_run_twice_in_one_process() -> None:
    """THE ISOLATION PROPERTY ITSELF, ASSERTED.

    Registration is append-only by design -- two failures sharing a code cannot
    be told apart, so the registry refuses duplicates. That makes every class
    definition a side effect, and a module whose import is not idempotent
    breaks any runner that imports it more than once: mutmut, pytest-xdist, a
    REPL session.
    """
    from cscie103_olap_oltp.remediation import _REGISTRY

    assert _REGISTRY["ERR_TEST_CONCRETE"] is ConcreteError
    assert _REGISTRY["ERR_TEST_EXAMPLE"] is ExampleError


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


def test_a_missing_code_reports_structured_facts() -> None:
    """THE CONTRACT IS THE FIELDS, NOT THE SENTENCE.

    `match="declares no code"` passed even with the rest of the message
    corrupted; asserting the whole sentence instead would make every wording
    improvement a test failure. A typed attribute is the stable machine
    contract -- prose stays free to improve.
    """
    with pytest.raises(ErrorCodeViolationError) as caught:

        class UnclassifiedError(ActionableError):
            pass

    assert caught.value.facts["offender"] == "UnclassifiedError"
    assert caught.value.reason == "missing"


def test_a_duplicate_code_names_the_incumbent() -> None:
    """WHICH CLASS ALREADY OWNS THE CODE IS THE WHOLE DIAGNOSIS.

    Knowing there is a collision tells the author to look; naming the incumbent
    tells them where. As a field it is assertable without pinning wording.
    """
    with pytest.raises(ErrorCodeViolationError) as caught:

        class DuplicateError(ActionableError):
            code = "ERR_TEST_EXAMPLE"

    assert caught.value.reason == "duplicate"
    assert caught.value.facts == {
        "offender": "DuplicateError",
        "code": "ERR_TEST_EXAMPLE",
        "incumbent": "ExampleError",
    }


def test_a_malformed_code_reports_what_was_rejected() -> None:
    with pytest.raises(ErrorCodeViolationError) as caught:

        class MalformedError(ActionableError):
            code = "something_wrong"

    assert caught.value.reason == "malformed"
    assert caught.value.facts == {"offender": "MalformedError", "code": "something_wrong"}


def test_the_bare_base_names_itself_in_the_refusal() -> None:
    """A mutant swapped `type(self).__name__` for `type(None).__name__`, so the
    refusal would have named NoneType -- a class the author never wrote."""
    with pytest.raises(NotImplementedError) as caught:
        raise ActionableError("bare", remediation="none")

    assert "ActionableError declares no code" in str(caught.value)
    assert "NoneType" not in str(caught.value)


def test_the_rendering_shows_every_fact_in_sorted_order() -> None:
    """THE CONTEXT LINE IS THE EVIDENCE, and it was only checked for presence.

    Sorted order is what makes two renderings of one failure comparable by eye,
    and mutants blanking or unsorting the facts survived.
    """
    error = ExampleError("broke", remediation="fix", zeta=1, alpha="a/b")

    assert str(error) == "[ERR_TEST_EXAMPLE] broke\n  context: alpha='a/b', zeta=1\n  fix: fix"


def test_the_rendering_omits_the_context_line_when_empty() -> None:
    """AN EMPTY CONTEXT LINE LOOKS LIKE MISSING DATA."""
    assert str(ExampleError("broke", remediation="fix")) == "[ERR_TEST_EXAMPLE] broke\n  fix: fix"


def test_each_violation_renders_a_message_naming_the_facts() -> None:
    """ONE ASSERTION PER TEMPLATE, WHICH THE RESTRUCTURING MADE POSSIBLE.

    The messages were adjacent string literals inside each raise, so every
    fragment was a separate mutable token: twenty mutants corrupted mid-sentence
    text and none could be killed without pinning the sentences word for word.

    AS TEMPLATES THERE IS ONE LITERAL PER FAILURE MODE, and asserting the
    rendering checks the thing that matters -- that the facts reach the reader
    -- without freezing the wording around them.
    """
    for reason, facts in [
        ("missing", {"offender": "Widget"}),
        ("malformed", {"offender": "Widget", "code": "bad"}),
        ("duplicate", {"offender": "Widget", "code": "ERR_X", "incumbent": "Other"}),
    ]:
        rendered = str(ErrorCodeViolationError(reason, **facts))

        assert rendered.startswith("Widget "), rendered
        for value in facts.values():
            assert str(value) in rendered, (reason, value)
        assert rendered.endswith("."), "a message a human reads is a sentence"


def test_the_missing_code_message_shows_the_abstract_escape_hatch() -> None:
    """THE ONE FACT THE TEMPLATE CARRIES THAT NO FIELD DOES.

    An author blocked by this guard needs the syntax for the legitimate
    exception; without it the only way forward is to invent a meaningless code,
    which is exactly what the guard exists to prevent.
    """
    assert "abstract=True" in VIOLATION_MESSAGES["missing"]


def test_the_malformed_message_states_the_convention() -> None:
    """A REJECTION THAT DOES NOT STATE THE RULE MAKES THE AUTHOR GUESS."""
    assert "ERR_" in VIOLATION_MESSAGES["malformed"]


def test_the_bare_base_message_names_the_class_it_refused() -> None:
    """A mutant swapped `type(self).__name__` for `type(None).__name__`, so the
    refusal would have named NoneType -- a class nobody wrote."""
    assert BARE_BASE_MESSAGE.format(name="Widget").startswith("Widget declares no code")


def test_a_code_must_satisfy_the_whole_convention_not_half_of_it() -> None:
    """EACH CASE VIOLATES EXACTLY ONE HALF, WHICH THE OLD FIXTURE DID NOT.

    "something_wrong" fails both the prefix and the case rule, so it was
    rejected whichever way the condition was written -- which is why flipping
    `and` to `or` survived. These inputs separate the halves.
    """
    for bad in ["BAD_CODE", "ERR_lower_case", "ERRNOUNDERSCORE", "ERR_", "err_x"]:
        with pytest.raises(ErrorCodeViolationError) as caught:
            type(f"Bad{abs(hash(bad))}", (ActionableError,), {"code": bad})

        assert caught.value.reason == "malformed", bad


def test_the_convention_accepts_the_codes_this_platform_uses() -> None:
    """A RULE THAT REJECTS REAL CODES IS A BROKEN RULE, so the pattern is
    checked against the ones actually in the registry."""
    for code in registered_codes():
        assert CODE_PATTERN.fullmatch(code), code


def test_the_registry_stores_the_class_not_merely_the_code() -> None:
    """THE VALUE IS LOAD-BEARING, AND ONLY THE KEYS WERE EVER ASSERTED.

    A mutant storing None survived twice. registered_codes() reads keys, so the
    catalogue looked healthy; and asserting against ExampleError could not
    catch it either, because that class registers at IMPORT time -- before the
    mutant applies -- so its entry was already correct.

    THE KILL NEEDS A CLASS REGISTERED DURING THE TEST. The value is what names
    the incumbent in a collision report; stored as None the author is sent to
    "NoneType", which is nowhere.
    """
    from cscie103_olap_oltp.remediation import _REGISTRY

    registered = type("RegisteredNow", (ActionableError,), {"code": "ERR_TEST_REGISTERED"})
    try:
        assert _REGISTRY["ERR_TEST_REGISTERED"] is registered

        with pytest.raises(ErrorCodeViolationError) as caught:
            type("Collides", (ActionableError,), {"code": "ERR_TEST_REGISTERED"})

        assert caught.value.facts["incumbent"] == "RegisteredNow"
    finally:
        # REGISTRATION IS A PROCESS-GLOBAL SIDE EFFECT, so a class created
        # inside a test must be withdrawn -- otherwise the second run in one
        # process collides, which is the defect that stopped mutmut entirely.
        _REGISTRY.pop("ERR_TEST_REGISTERED", None)
