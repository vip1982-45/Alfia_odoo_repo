# Alfia registrations, external semen and incidents (RFP REQ-HOR-03, REQ-SEM-04, REQ-SAF-01)

## Registrations and certificates (Stud › Breeding › Registrations and certificates)
- Requests for studbook registration, DNA sample/parentage, passport, breeding certificate or other certificate, to an
  authority or laboratory, with request and due dates. Raised by staff, approved by a breeding manager who did not raise
  it (an activity then follows the due date), sent, and completed with the certificate number or the DNA result.
  Overdue requests are flagged and filterable. Evidence is attached to the request.
- The horse shows its studbook number and DNA status (none, pending, verified, not verified).

## External semen (Stud › Breeding › External semen statements / Import centre statement)
- Mark a stock location as an external centre (its *External semen centre* field) and ship straws to it with ordinary
  transfers.
- Import the centre's statement as CSV: Date, Batch, Movement (received, used, shipped, balance), Quantity, Reference.
  Every line is checked: batch exists, quantity valid, *received* matches our shipments, *used/shipped* within what we
  hold there, *balance* equals our stock there. Lines are OK, exceptions (with the reason) or duplicates (already posted
  from an earlier statement); the same file cannot be imported twice. The source file is attached to the statement.
- A custodian may accept an exception with a reason. Another person posts the statement: used straws move to *Semen used
  at external centres*, shipped ones to customers. Exceptions stay in the log and are not posted.

## Incidents (Incidents app, for everyone)
- Anyone reports an incident: kind, severity, where, horse or person involved, what happened and the immediate action.
  High and critical ones alert an incident manager (breeding, training, vet and HR managers have this role).
- A manager assigns an investigator; findings are recorded (a root cause is required for high or critical). Corrective
  actions have owners and due dates (each owner gets an activity) and are marked done with evidence.
- Closure needs every action done and closure notes, and is approved by an incident manager who neither reported nor
  investigated it. A closed incident cannot be changed. Reporters see their own incidents, investigators and action
  owners see theirs.
