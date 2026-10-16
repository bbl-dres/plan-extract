/* The API documentation page (/api/docs): the app's frame in the page's language (Help, language, Log in), then
   Swagger UI over the server's OpenAPI description. "Try it out" sends real requests to this server: the workflow
   endpoints answer, the batch endpoints answer 401 until API tokens exist. */
import { $ } from '/js/core.js';
import { dialog, langSelect } from '/js/chrome.js';
import { applyTexts, loadTexts } from '/js/i18n.js';

await loadTexts();
applyTexts();
dialog('help', '#btn-help');
dialog('login', '#btn-login');
langSelect($('#lang-select'));
window.ui = SwaggerUIBundle({
  url: '/api/openapi.json',
  dom_id: '#swagger',
  deepLinking: true,
  docExpansion: 'list',
  defaultModelsExpandDepth: 0,
  displayRequestDuration: true,
});
