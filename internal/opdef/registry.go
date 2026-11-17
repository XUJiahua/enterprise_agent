package opdef

// This file is the protected platform event registry. Changing it changes the
// embedded SourceFiles set and needs owner review like the rest of the catalog.
//
// consumedEvents lists the PurchaseEvent types that a registered notification
// consumer handles; exemptOperations lists the operations allowed to carry an
// EventExemption instead of an event. Validate's eventRule (opdef.go) reads
// both: every non-exempt entry must declare an event that is registered here,
// and only an operation on exemptOperations may replace its event with an
// EventExemption. The registry is embedded in SourceFiles so the catalog drift
// check (appctl operations) requires a checkout's registry.go to be identical
// to the one the Verifier was built from.
//
// consumedEvents mirrors the notification outbox consumer, the notify package's
// audience() switch. That consumer is not a package in this repo's internal/:
// it lives only in the frozen per-phase benchmark task fixtures
// (benchmark/phaseN/source/notify.go.txt), and each phase's fixture lists just
// that phase's subset of events (phase6a has purchase.countersigned, phase6b
// has purchase.reassigned, phase5/5b have neither). There is no single runtime
// notify package here to diff against, and the frozen fixtures must not change,
// so registry.go is the authoritative consumer list for the public catalog.
// registry_test.go keeps it honest against the catalog in both directions:
// every non-exempt entry's event is registered, every registered event is
// consumed by some entry, and exemptions stay confined to exemptOperations.
// purchase.countersigned and purchase.reassigned are catalog events whose
// consumer previously existed only in a subset of the phase fixtures; listing
// them here registers a consumer for them in the protected boundary.

// consumedEvents are the event types a registered consumer drains from the
// PurchaseEvent outbox. Keep sorted for readability; order is not significant.
var consumedEvents = []string{
	"purchase.countersigned",
	"purchase.reassigned",
	"purchase.rejected",
	"purchase.reopened",
}

// exemptOperations may carry an EventExemption instead of an event. Only the
// historical first approval, which predates the outbox, is exempt.
var exemptOperations = []Name{
	BulkApprove,
}
