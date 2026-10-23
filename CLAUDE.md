# tempo-tracker

## Jira / Tempo
- This app writes to Tempo (the send flow) and reads from Jira. Never trigger a write
  (sending worklogs, creating or changing anything in Jira/Tempo) without my explicit
  permission for that action, also not "to test it".
- Testing against the real APIs: read calls only (myself, search, GET worklogs).
- Tests stay off the network; mock Jira and Tempo.
- Meetings come from worklogs I log in Tempo myself. No calendar integration.
