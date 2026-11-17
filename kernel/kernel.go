// Package kernel is the domain-free contract of the operation kernel: who is
// calling (Principal), what an operation declares (Definition), what a domain
// Normalize/Execute step returns (Input, Outcome) and how failures are
// classified (Error). It has no database, HTTP or business dependency; the
// PostgreSQL runner lives in kernel/pg and business rules live in the domain
// module that registers operations.
package kernel

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
	"unicode"
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
	Name    string `json:"name"`
	Version int    `json:"version"`
}

func (r Ref) String() string { return fmt.Sprintf("%s@%d", r.Name, r.Version) }

// EventType is one versioned event an operation may emit.
type EventType struct {
	Type    string `json:"type"`
	Version int    `json:"version"`
}

func (e EventType) String() string { return fmt.Sprintf("%s@%d", e.Type, e.Version) }

// Definition is the reviewed declaration of an operation version. An
// operation either declares the events it may emit or gives NoEventReason;
// every target always gets exactly one audit entry. Changing what a version
// means requires a new Version.
type Definition struct {
	Ref           Ref         `json:"ref"`
	Targets       string      `json:"targets"`                 // top-level request field holding the target id or id array
	MaxTargets    int         `json:"maxTargets"`              // 1..MaxTargets distinct targets per call
	Events        []EventType `json:"events,omitempty"`        // events the Outcome may contain
	NoEventReason string      `json:"noEventReason,omitempty"` // explicit exemption when Events is empty
}

// Validate checks the declaration itself; it does not inspect domain code.
func (d Definition) Validate() error {
	if !validName(d.Ref.Name) || d.Ref.Version < 1 {
		return fmt.Errorf("invalid operation ref %q", d.Ref)
	}
	if d.Targets == "" {
		return fmt.Errorf("%s: Targets must name the request field holding the targets", d.Ref)
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

// FingerprintVersion prefixes RequestFingerprint values. Ledger rows without
// it were written by v0.1 (Fingerprint over the normalized Input).
const FingerprintVersion = "v2:"

// RequestFingerprint binds a request to its operation version and to every
// field of the raw JSON object, so a field the domain's Normalize forgets
// still separates two requests. Only the declared targets field is compared
// as a set (it must equal targets: a string for one target, or an array);
// every other value, including array order, is kept as sent. Duplicate keys
// at any depth, including keys that differ only in case (encoding/json
// matches field names case-insensitively), are Invalid, so the fingerprint and
// Normalize cannot read different values.
func RequestFingerprint(ref Ref, targetsField string, raw []byte, targets []string) (string, error) {
	if err := noDuplicateKeys(raw); err != nil {
		return "", err
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber() // keep numbers exactly as sent
	var body map[string]any
	if err := dec.Decode(&body); err != nil || body == nil {
		return "", Invalidf("request must be a JSON object")
	}
	sorted := slices.Clone(targets)
	slices.Sort(sorted)
	match := false
	switch x := body[targetsField].(type) {
	case string:
		match = len(sorted) == 1 && x == sorted[0]
	case []any:
		ids := make([]string, 0, len(x))
		for _, e := range x {
			if s, ok := e.(string); ok {
				ids = append(ids, s)
			}
		}
		slices.Sort(ids)
		if len(ids) == len(x) && slices.Equal(ids, sorted) {
			body[targetsField], match = ids, true
		}
	}
	if !match {
		return "", Internalf("%s: Normalize targets differ from request field %q", ref, targetsField)
	}
	canonical, err := json.Marshal(body) // map keys are sorted
	if err != nil {
		return "", Internalf("%s: %v", ref, err)
	}
	return FingerprintVersion + Fingerprint(ref, Input{Targets: sorted, Payload: canonical}), nil
}

// foldKey maps each rune to the smallest rune of its Unicode simple-fold
// orbit, so keys encoding/json would match to the same field (e.g. "reason",
// "Reason", "reaſon") get the same key.
func foldKey(s string) string {
	var b strings.Builder
	for _, r := range s {
		min := r
		for f := unicode.SimpleFold(r); f != r; f = unicode.SimpleFold(f) {
			if f < min {
				min = f
			}
		}
		b.WriteRune(min)
	}
	return b.String()
}

// noDuplicateKeys rejects any object, at any depth, with two keys that are
// equal under Unicode simple case folding.
func noDuplicateKeys(raw []byte) error {
	dec := json.NewDecoder(bytes.NewReader(raw))
	var walk func() error
	walk = func() error {
		t, err := dec.Token()
		if err != nil {
			return err
		}
		switch t {
		case json.Delim('{'):
			seen := map[string]bool{}
			for dec.More() {
				k, err := dec.Token()
				if err != nil {
					return err
				}
				key := foldKey(k.(string))
				if seen[key] {
					return Invalidf("duplicate field %q", k)
				}
				seen[key] = true
				if err := walk(); err != nil {
					return err
				}
			}
			_, err = dec.Token()
			return err
		case json.Delim('['):
			for dec.More() {
				if err := walk(); err != nil {
					return err
				}
			}
			_, err = dec.Token()
			return err
		}
		return nil
	}
	if err := walk(); err != nil {
		if CodeOf(err) == Invalid {
			return err
		}
		return Invalidf("malformed request")
	}
	return nil
}

// UniformDenial is the only message a client sees for a Denied operation, so
// a denial never reveals whether or why a target is ineligible.
const UniformDenial = "not permitted, or one or more targets are not eligible"

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
