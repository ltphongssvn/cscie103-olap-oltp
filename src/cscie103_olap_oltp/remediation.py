# src/cscie103_olap_oltp/remediation.py
"""A failure is data: a stable code, the facts, and what to do about it.

THE ASYMMETRY THIS FIXES. The policy layer already emits structured violations
with ids and reason codes, because a Rego denial is data by construction. The
Python layer did not: it raised RuntimeError with a sentence. So half this
platform's failures were queryable and half were strings, and which half you got
depended on where the failure happened rather than on how serious it was.

    Findings as Data      the failure has an identity that survives rewording
    Remediation as Data   the fix travels with the failure, not in a README

WHY A CODE AND NOT JUST A GOOD MESSAGE. A message can be reworded freely, and
should be -- clarity improves. A code is the join key: it is what lets two
occurrences six months apart be recognised as the same failure, counted, and
matched against a runbook. Prose cannot be aggregated.

WHY CONTEXT IS FIELDS RATHER THAN INTERPOLATION. `f"could not lock {path}"`
makes the path unqueryable: nothing can ask which paths failed this way without
parsing English back apart. The same fact as a field is filterable.

WHY THE GUARD RUNS AT CLASS-DEFINITION TIME, HAVING FIRST BEEN A TEST.

The first design walked ActionableError.__subclasses__() in the test suite and
asserted every code was present and unique. It failed on its own fixtures: five
tests each defined a throwaway subclass, every one registered, and the
uniqueness check saw five duplicates.

Filtering the fixtures out would have taught the guard to ignore the only
violations it could see, and left the real hole open -- nothing prevented a
genuine duplicate, because the check ran only when someone ran the suite.

The current practice is to validate during subclass creation. __init_subclass__
fires as the class statement executes, so a missing, malformed or duplicated
code is a definition-time TypeError -- in a test, a notebook, a REPL or
production. The invariant no longer depends on being tested.

__init_subclass__ RATHER THAN A METACLASS, deliberately: it covers this case
entirely, and reaching for a metaclass here would add machinery a reader has to
learn for no additional guarantee.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ActionableError",
    "Finding",
    "registered_codes",
]

# THE EXPLICIT REGISTRY, NOT A WALK OF __subclasses__.
#
# __subclasses__ never forgets: a class defined once inside a function lingers
# for the process lifetime, so a registry derived from it reports garbage in any
# process that ever defined a throwaway subclass. Registering deliberately means
# the contents are exactly what was declared.
_REGISTRY: dict[str, type[ActionableError]] = {}


class Finding(BaseModel):
    """One structured failure, in the shape the rest of the platform speaks.

    DELIBERATELY THE SAME SHAPE AS A POLICY VIOLATION -- an id, a message, and
    the facts -- so a Python failure and a Rego denial land in one queryable
    stream rather than two that have to be joined by hand.

    FROZEN, BECAUSE AN OBSERVED FACT DOES NOT CHANGE. Mutating a recorded
    failure after the fact is how a record stops matching what happened.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    message: str
    remediation: str
    context: dict[str, Any] = {}


class ActionableError(Exception):
    """An error that knows its own identity and its own fix.

    EVERY CONCRETE SUBCLASS DECLARES A UNIQUE, CONVENTIONAL CODE, and that is
    enforced as the subclass is created rather than asked for in review.
    """

    # EMPTY ON THE BASE, ON PURPOSE. Raising the base directly would produce an
    # unclassified failure, which is the thing this module exists to prevent.
    code: str = ""

    def __init_subclass__(cls, abstract: bool = False, **kwargs: Any) -> None:
        """Validate and register, as the class statement executes.

        `abstract=True` EXEMPTS AN INTERMEDIATE LAYER. A base that exists to
        group related failures is not itself a failure mode, and forcing a code
        onto it would mean inventing a meaningless one -- which pollutes exactly
        the stream this module keeps clean.

        super() IS CALLED FIRST so cooperative multiple inheritance keeps
        working; skipping it is the classic way this hook breaks a hierarchy
        nobody expected to be involved.
        """
        super().__init_subclass__(**kwargs)

        if abstract:
            return

        code = cls.__dict__.get("code", "")
        if not code:
            raise TypeError(
                f"{cls.__name__} declares no code. Every ActionableError "
                "subclass must declare one, or the failure cannot be "
                "aggregated. Use `class X(ActionableError, abstract=True)` for "
                "an intermediate base."
            )

        if not (code.startswith("ERR_") and code.isupper()):
            raise TypeError(
                f"{cls.__name__} has code {code!r}; codes are upper case and "
                "begin with ERR_ so they are greppable across every output this "
                "repository writes"
            )

        if code in _REGISTRY:
            raise TypeError(
                f"{cls.__name__} uses code {code!r}, already registered by "
                f"{_REGISTRY[code].__name__}. Two failures sharing a code "
                "cannot be told apart."
            )

        _REGISTRY[code] = cls

    def __init__(self, message: str, *, remediation: str, **context: Any) -> None:
        if not type(self).code:
            raise NotImplementedError(
                f"{type(self).__name__} declares no code; raise a subclass "
                "rather than ActionableError itself"
            )

        super().__init__(message)
        self.message = message
        self.remediation = remediation
        self.context = context

    def __str__(self) -> str:
        """Every part, because different readers need different ones.

        The person at the terminal needs the sentence and the fix; a log needs
        the code; a triage query needs the facts. Rendering one and dropping the
        others forces a choice that does not have to be made.
        """
        rendered = f"[{self.code}] {self.message}"
        if self.context:
            facts = ", ".join(f"{key}={value!r}" for key, value in sorted(self.context.items()))
            rendered += f"\n  context: {facts}"
        return f"{rendered}\n  fix: {self.remediation}"

    def as_finding(self) -> Finding:
        """The queryable form, for artifacts and the ledger."""
        return Finding(
            id=self.code,
            message=self.message,
            remediation=self.remediation,
            context=dict(self.context),
        )


def _import_every_module() -> None:
    """Execute every module in this package, so every error class exists.

    A CLASS STATEMENT ONLY RUNS ON IMPORT, so a registry read before an import
    reports a SUBSET and looks perfectly healthy doing it. Observed, not
    anticipated: a probe printed two codes and silently omitted
    ERR_LEDGER_LOCKED because that module had not been imported in the process.
    A partial catalogue is worse than none -- it answers confidently and wrongly.

    DISCOVERED BY TRAVERSAL, NOT BY A HAND-MAINTAINED LIST. The obvious fix was
    a tuple of module names, and that is the same drift in a new place: adding
    an error module means remembering to add it there, and forgetting is silent.
    Walking the package cannot forget.

    NOT ENTRY POINTS, WHICH ARE THE OTHER DOCUMENTED APPROACH. Those exist for
    plugins shipped as SEPARATE distributions and announced through package
    metadata. Every error here lives in this one package, so traversal is the
    technique that fits; entry points would add packaging ceremony for a problem
    we do not have.

    SAFE BECAUSE THIS PACKAGE HAS NO IMPORT-TIME SIDE EFFECTS -- every entry
    point sits behind `if __name__ == "__main__"`. That is a real precondition,
    stated because traversal would otherwise execute work nobody asked for.
    """
    import importlib
    import pkgutil

    import cscie103_olap_oltp

    for module in pkgutil.walk_packages(
        cscie103_olap_oltp.__path__,
        prefix=f"{cscie103_olap_oltp.__name__}.",
    ):
        importlib.import_module(module.name)


def registered_codes() -> list[str]:
    """Every code this platform can raise, in declaration order.

    UNIQUENESS IS ALREADY GUARANTEED at definition time, so this reports rather
    than checks -- but a report that depends on what the caller happened to
    import is not a catalogue.
    """
    _import_every_module()
    return list(_REGISTRY)
