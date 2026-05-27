# ANS Prospect Tool — credential storage package.
#
# All credential reads/writes go through this package. No other module
# in the application calls `keyring` directly; that keeps the keychain
# integration auditable from one place.
