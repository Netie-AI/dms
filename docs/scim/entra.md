# Microsoft Entra ID provisioning (SCIM 2.0)

This is the path an Entra administrator uses to create, update, and deactivate
DMS users. Okta and Google Workspace can use the same URL and the same bearer.
They are ordinary SCIM clients. There is no per-company rule in the product.

`DMS_SCIM` must be the digit `1` on the DMS API. Any other value, including
`true` or a padded ` 1 `, leaves `/scim/v2` unmounted. The Studio panel also
needs `DMS_GOV_CONSOLE=1`, because it sits inside the governance console.

## 1. Enterprise application

1. In the Entra admin center, open Enterprise applications.
2. New application, then Create your own application.
3. Name it, and choose Integrate any other application you don't find in the gallery.
4. Open Provisioning, then Get started, and set the mode to Automatic.

## 2. Tenant URL and token

1. In DMS Studio, open the governance console as a manager.
2. In Directory sync, choose Issue token. The token is shown once. DMS stores
   only a hash and an OpenVault reference. It does not keep a second copy.
3. Tenant URL: `https://<your-dms-host>/scim/v2`
4. Secret token: paste the token from step 2.
5. Test connection. Entra calls `/scim/v2/ServiceProviderConfig`. A 401 means
   the token does not match the current hash. Issue a new token and paste it
   again. The previous token stops working.

Rotate from the same panel when someone who saw the token should no longer
have it. Rotation is `POST /v1/scim/admin/token`. A viewer cannot call it.

## 3. Attribute mappings

Under Mappings, open Provision Microsoft Entra ID Users. Keep these mappings.
Delete the rest if Entra sends attributes this API does not store. Unknown
attributes are ignored. `password` is ignored and is not stored.

| Entra attribute | DMS attribute |
|-----------------|---------------|
| userPrincipalName | userName |
| Switch([IsSoftDeleted], , "False", "True", "True", "False") | active |
| displayName | displayName |
| mail | emails[type eq "work"].value |
| department | urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department |
| manager | urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:manager |

Groups use `displayName` and `members`. A department name or a group display
name is matched exactly, ignoring case. `Finance` does not match `Finance Ops`.
The manager configures that match in Directory sync: role, governance role,
daily token budget, and space ids. A person with no matching row becomes a
viewer and keeps the catalog. The lowest priority number wins when two rows
match.

## 4. Provision on demand

1. Save the mappings.
2. Provisioning, then Provision on demand.
3. Pick one user and start provisioning.
4. Entra shows Success when the POST or PATCH returns the user. The body
   includes `userName`, `active`, and the enterprise `department`.
5. In DMS, Directory sync and the admin user list show that person's role and
   spaces. A mapped department assigns them on this call.

## 5. Leavers

Entra sends `active` false, or DELETE. DELETE does not remove the row. The
user stays, with `active` false, so a later GET and a `userName eq` filter
still find them and a later PATCH can set `active` true. The same call revokes
DMS sessions. The next login is refused until the account is active again.

Provisioning runs on Entra's cycle (the default is about 40 minutes). On-demand
provisioning is the same write, immediately. There is no separate DMS poll.
