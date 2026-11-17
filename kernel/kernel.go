// Package kernel is the domain-free contract of the operation kernel: who is
// calling (Principal), what an operation declares (Definition), what a domain
// Normalize/Execute step returns (Input, Outcome) and how failures are
// classified (Error). It has no database, HTTP or business dependency; the
// PostgreSQL runner lives in kernel/pg and business rules live in the domain
// module that registers operations.
package kernel

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
	"unicode/utf8"
)

// Principal is the caller identity bound by the trusted host (authentication
// adapter). It is never read from a request body. Roles are opaque to the
// kernel; the domain's Authorize interprets them.
type Principal struct {
	Tenant string
	Actor  string
	Roles  []string
}

// Has reports whether the principal carries role.
func (p Principal) Has(role string) bool { return slices.Contains(p.Roles, role) }

// Ref names one operation version, e.g. {"procurement.approve", 1}.
type Ref struct {
	Name    string
	Version int
}

func (r Ref) String() string { return fmt.Sprintf("%s@%d", r.Name, r.Version) }

// EventType is one versioned event an operation may emit.
type EventType struct {
	Type    string
	Version int
}

func (e EventType) String() string { return fmt.Sprintf("%s@%d", e.Type, e.Version) }

// Definition is the reviewed declaration of an operation version. An
// operation either declares the events it may emit or gives NoEventReason;
// every target always gets exactly one audit entry. Changing what a version
// means requires a new Version.
type Definition struct {
	Ref           Ref
	MaxTargets    int         // 1..MaxTargets distinct targets per call
	Events        []EventType // events the Outcome may contain
	NoEventReason string      // explicit exemption when Events is empty
}

// Validate checks the declaration itself; it does not inspect domain code.
func (d Definition) Validate() error {
	if !validName(d.Ref.Name) || d.Ref.Version < 1 {
		return fmt.Errorf("invalid operation ref %q", d.Ref)
	}
	if d.MaxTargets < 1 || d.MaxTargets > 1000 {
		return fmt.Errorf("%s: MaxTargets must be 1..1000", d.Ref)
	}
	if (len(d.Events) == 0) == (strings.TrimSpace(d.NoEventReason) == "") {
		return fmt.Errorf("%s: declare events or a NoEventReason, not both or neither", d.Ref)
	}
	seen := map[EventType]bool{}
	for _, e := range d.Events {
		if !validName(e.Type) || e.Version < 1 || seen[e] {
			return fmt.Errorf("%s: invalid or duplicate event %s", d.Ref, e)
		}
		seen[e] = true
	}
	return nil
}

// Clone returns a copy that shares no slices with d.
func (d Definition) Clone() Definition {
	d.Events = slices.Clone(d.Events)
	return d
}

// Input is a normalized request: the target IDs and the canonical JSON of
// every other meaningful field (all of it is fingerprinted).
type Input struct {
	Targets []string
	Payload json.RawMessage
}

// Audit is the change recorded for one target.
type Audit struct {
	Target string
	Change json.RawMessage // JSON object, domain-defined (e.g. {"status":{"from":..,"to":..}})
}

// Event is one outbox entry produced by the operation.
type Event struct {
	EventType
	Target  string
	Payload json.RawMessage // JSON object
}

// Outcome is what the domain reports after changing data; the runner checks
// it against the Input and Definition before anything is committed.
type Outcome struct {
	Targets []string // the targets actually changed
	Audit   []Audit
	Events  []Event
	Result  any // client-visible result; serialize only allowed fields
}

// Targets sorts ids and rejects an empty, oversized or duplicated set.
func Targets(ids []string, max int) ([]string, error) {
	if len(ids) < 1 || len(ids) > max {
		return nil, Invalidf("between 1 and %d targets required", max)
	}
	out := slices.Clone(ids)
	slices.Sort(out)
	for i, id := range out {
		if id == "" || len(id) > 100 || !utf8.ValidString(id) || strings.ContainsRune(id, 0) {
			return nil, Invalidf("invalid target id")
		}
		if i > 0 && out[i-1] == id {
			return nil, Invalidf("duplicate target id")
		}
	}
	return out, nil
}

// Fingerprint binds a request to its operation version, payload and sorted
// targets. Length prefixes keep field boundaries unambiguous.
func Fingerprint(ref Ref, in Input) string {
	h := sha256.New()
	part := func(s string) { fmt.Fprintf(h, "%d:%s", len(s), s) }
	part(ref.Name)
	part(fmt.Sprint(ref.Version))
	part(string(in.Payload))
	targets := slices.Clone(in.Targets)
	slices.Sort(targets)
	for _, t := range targets {
		part(t)
	}
	return hex.EncodeToString(h.Sum(nil))
}

// ValidKey reports whether key is an acceptable idempotency key.
func ValidKey(key string) bool {
	if len(key) < 1 || len(key) > 128 {
		return false
	}
	for _, c := range key {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("._:-", c)) {
			return false
		}
	}
	return true
}

func validName(s string) bool {
	if len(s) < 1 || len(s) > 100 {
		return false
	}
	for _, c := range s {
		if !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || strings.ContainsRune("._-", c)) {
			return false
		}
	}
	return true
}

// Code classifies a failure independently of transport; entry points map it
// (e.g. to HTTP status).
type Code string

const (
	Invalid   Code = "invalid"   // malformed request, unknown operation
	Denied    Code = "denied"    // missing identity, not permitted, or ineligible target (uniform)
	Conflict  Code = "conflict"  // idempotency key reused with a different request
	Retryable Code = "retryable" // transient (deadlock, serialization) or commit outcome unknown: retry with the same key
	Internal  Code = "internal"  // bug or contract violation; nothing was committed
)

// Error is a classified failure. Msg is safe to show clients; Err is not.
type Error struct {
	Code Code
	Msg  string
	Err  error
}

func (e *Error) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("%s: %s: %v", e.Code, e.Msg, e.Err)
	}
	return fmt.Sprintf("%s: %s", e.Code, e.Msg)
}

func (e *Error) Unwrap() error { return e.Err }

func Invalidf(format string, a ...any) error {
	return &Error{Code: Invalid, Msg: fmt.Sprintf(format, a...)}
}
func Deniedf(format string, a ...any) error {
	return &Error{Code: Denied, Msg: fmt.Sprintf(format, a...)}
}
func Conflictf(format string, a ...any) error {
	return &Error{Code: Conflict, Msg: fmt.Sprintf(format, a...)}
}
func Internalf(format string, a ...any) error {
	return &Error{Code: Internal, Msg: fmt.Sprintf(format, a...)}
}

// CodeOf returns the classification of err; unclassified errors are Internal.
func CodeOf(err error) Code {
	if err == nil {
		return ""
	}
	var e *Error
	if errors.As(err, &e) {
		return e.Code
	}
	return Internal
}
