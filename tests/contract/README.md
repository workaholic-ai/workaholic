# Contract Tests

Contract tests verify observable behavior shared by multiple implementations or
boundaries. Examples include CLI envelope conformance, persistence-adapter
semantics, LocalSession and RemoteSession equivalence, and protocol
negotiation.

Tests in this directory carry the `contract` marker. They may use lightweight
real implementations but must not pass by asserting only against a mock of the
contract being verified. Backend-independent assertions belong here; physical
schema and private route details do not.

The canonical end-to-end acceptance flows remain in the
[golden journey directory](../e2e/golden/README.md).
