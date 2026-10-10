# A-2: local custody recovery triage

`list_custody_recovery_items` combines existing outbox records with the
most recent local custody lookup observation. It supports bounded cursor
pagination without network operations, database writes, or automated replay.

Items with an unverified remote claim or a quarantined outbox state require
operator review. Items without a lookup or with a blocked lookup need
independent status investigation. A lookup claiming absence can inform
recovery planning but does not independently prove absence. Any recorded
lookup result, including `not_found_unverified`, therefore requires explicit
operator review rather than being presented as safe to retry. Items without
a prior lookup remain marked `lookup`.

This view never authenticates an operator, validates external custody,
authorizes a replay, or authorizes inference. A-2 is still incomplete until
a separately administered durable service and independently verified receipt
workflow are integrated. A-3 covers signature and key validation.
