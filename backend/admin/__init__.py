# ANS Prospect Tool — admin/settings route package.
#
# Hosts the small set of "console" endpoints that the single user
# interacts with to configure the app: credentials, M365 connection
# (step 13), audit views, and so on.
#
# Every route in this package depends on `current_username` from
# backend.auth.sessions — anonymous callers get 401.
