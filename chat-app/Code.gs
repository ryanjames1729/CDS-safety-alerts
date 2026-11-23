/**
 * Safety Alerts commands for Google Chat.
 *
 * Lets people in the space type /update or /help. This script does not look
 * anything up itself: it starts the "On request" workflow in the GitHub
 * repository, which posts the reply to the space through the usual webhook.
 *
 * Setup steps are in chat-app/README.md.
 */

const REPO = 'ryanjames1729/CDS-safety-alerts';
const WORKFLOW = 'status.yml';
const BRANCH = 'main';

// These must match the Command IDs entered on the Chat API Configuration page.
const UPDATE_COMMAND_ID = 1;
const HELP_COMMAND_ID = 2;

const USAGE =
  'Type */update* for current weather and road conditions, or */help* for a guide to reading the alerts.';

/** Runs when someone uses a slash command. */
function onAppCommand(event) {
  const id = Number(event.chat.appCommandPayload.appCommandMetadata.appCommandId);
  if (id === UPDATE_COMMAND_ID) return request_('update');
  if (id === HELP_COMMAND_ID) return request_('help');
  return reply_(USAGE);
}

/** Runs when someone @mentions the app or messages it directly. */
function onMessage(event) {
  const message = (event.chat.messagePayload && event.chat.messagePayload.message) || {};
  const text = String(message.text || '').toLowerCase();
  if (text.indexOf('help') !== -1) return request_('help');
  if (text.indexOf('update') !== -1 || text.indexOf('status') !== -1) return request_('update');
  return reply_(USAGE);
}

/** Runs when the app is added to a space. */
function onAddedToSpace(event) {
  return reply_('Safety Alerts commands are ready. ' + USAGE);
}

/** Asks GitHub to post an update or the guide, and tells the person what to expect. */
function request_(command) {
  const code = startWorkflow_(command);
  if (code === 204) {
    return reply_(
      command === 'help'
        ? 'On it. The guide will post in the alerts space in about 20 seconds.'
        : 'On it. Current conditions will post in the alerts space in about 30 seconds.'
    );
  }
  if (code === 0) {
    return reply_('Commands are not finished being set up: the GITHUB_TOKEN script property is missing.');
  }
  return reply_(
    'Could not start that request (GitHub answered ' + code + '). ' +
    'Please tell whoever maintains the alert system; alerts themselves are not affected.'
  );
}

/** Returns the HTTP status from GitHub (204 means started), or 0 if no token is stored. */
function startWorkflow_(command) {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) return 0;
  const url = 'https://api.github.com/repos/' + REPO + '/actions/workflows/' + WORKFLOW + '/dispatches';
  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({ ref: BRANCH, inputs: { command: command } }),
    muteHttpExceptions: true,
  });
  const code = response.getResponseCode();
  if (code !== 204) console.error('GitHub dispatch failed: ' + code + ' ' + response.getContentText());
  return code;
}

function reply_(text) {
  return { hostAppDataAction: { chatDataAction: { createMessageAction: { message: { text: text } } } } };
}

/**
 * Run this once from the Apps Script editor (select testConnection, then Run).
 * It asks for permission to contact GitHub, then requests a real update, so a
 * conditions update should appear in the alerts space within about 30 seconds.
 */
function testConnection() {
  const code = startWorkflow_('update');
  if (code === 204) console.log('Success: GitHub accepted the request. Watch the alerts space.');
  else if (code === 0) console.log('No GITHUB_TOKEN script property found. Add it under Project Settings.');
  else console.log('GitHub answered ' + code + '. 401 or 403 means the token is wrong or lacks Actions write access; 404 means the repository or workflow name is wrong.');
}
