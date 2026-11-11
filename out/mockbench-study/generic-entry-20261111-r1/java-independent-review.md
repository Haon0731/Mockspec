# Java generic-backend independent review

Status: **passed_bounded for the two-case targeted intake gate**. Every new
relation remains `not_evaluated`.

The final `java-zipkin-direct-r6` and `java-zipkin-direct-r7` runs match the
current driver and WALA probe hashes. They preserve two distinct frozen Zipkin
identities and the `java-dual-region-v1` masks, compile only the masked test plus
admitted `src/main` sources, do not execute the target, and do not rehash the
dependency set.

Both runs map both original assertions exactly. Each has one separately counted
unique call binding and one accepted observation-linked cross-source relation:

| Frozen case | Exact observation root | Product endpoint | Typed path |
| --- | --- | --- | --- |
| Cassandra 3.11.3 | `SchemaTest.java:38`, `assertSame` | `Schema.java:103`, visible return | `2 -> 3 -> 5 -> 8 -> 6`, four `DATA_DEP` edges |
| Cassandra 3.11.4 | `SchemaTest.java:39`, `assertSame` | `Schema.java:103`, visible return | `2 -> 3 -> 5 -> 8 -> 6`, four `DATA_DEP` edges |

The edge direction is source to dependent: product checkcast, product return,
callee return, caller return, then the exact assertion seed. Neither traversal
was truncated. The second original `assertNotNull` observation is retained and
mapped but correctly has no product relation because it observes the structural
harness parameter directly.

The placeholder boundary is explicit in the final implementation. All admitted
compile sources are scanned for the narrow single-throw placeholder form;
detected spans are retained in provenance and any typed path crossing one is
suppressed. These two accepted paths cross no placeholder span.

This gate is narrow. Both cases are snapshots from one project and exercise the
same product method and test shape. WALA uses `NO_HEAP`, no control dependence,
reflection disabled, and context-insensitive predecessor traversal without
matched call/return enforcement. The evidence therefore does not establish path
feasibility, heap/state/control/exception influence, concrete expected values,
semantic correctness, or broad Java generalization.

Machine-readable hashes, identities, endpoints, edges, and retained limitations
are in [`java-independent-review.json`](java-independent-review.json).
