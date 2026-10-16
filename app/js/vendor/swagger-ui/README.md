# Swagger UI (vendored)

Swagger UI **5.33.0**, Apache-2.0 licence (`LICENSE`, `NOTICE`), from the npm package `swagger-ui-dist`
(`https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-5.33.0.tgz`, integrity checked against the registry's
sha512):

| File | Origin |
|---|---|
| `swagger-ui-bundle.js` | as published; the licences of the libraries bundled in it are in `swagger-ui-bundle.js.LICENSE.txt` |
| `swagger-ui.css` | as published |

Only the API documentation page uses it (`../../../api-docs.html`, served at `/api/docs`, set up by
`../../api-docs.js`); it renders `../../../openapi.json`. Vendored rather than loaded from a CDN, so the page sends
its visitors to no third party.

To update: fetch the same files of a newer `swagger-ui-dist` release and change the version here.
