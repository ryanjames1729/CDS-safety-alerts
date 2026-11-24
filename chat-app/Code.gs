/**
 * Safety Alerts helper for Google Apps Script. It does two jobs:
 *
 * 1. Clock. Every 5 minutes it starts a monitoring run in the GitHub
 *    repository. GitHub's own scheduler proved too unreliable for this.
 * 2. Commands. It lets people in the Chat space type /update or /help.
 *
 * This script does not look anything up itself. It only starts workflows in
 * the GitHub repository, which post to the space through the usual webhook.
 *
 * Setup steps are in chat-app/README.md.
 */

const REPO = 'ryanjames1729/CDS-safety-alerts';
const BRANCH = 'main';
const MONITOR_WORKFLOW = 'alerts.yml'; // the alert monitor
const REQUEST_WORKFLOW = 'status.yml'; // "On request": update and help

// These must match the Command IDs entered on the Chat API Configuration page.
const UPDATE_COMMAND_ID = 1;
const HELP_COMMAND_ID = 2;

const USAGE =
  'Type */update* for current weather and road conditions, or */help* for a guide to reading the alerts.';

// --- Clock ------------------------------------------------------------------

/**
 * Run this once from the editor (select installClock, then Run) to start the
 * 5-minute clock. Running it again is safe: it replaces the existing clock.
 */
function installClock() {
  removeClock();
  ScriptApp.newTrigger('tick').timeBased().everyMinutes(5).create();
  console.log('Clock installed: a monitoring run will start every 5 minutes.');
}

/** Run this from the editor to stop the clock. */
function removeClock() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === 'tick') ScriptApp.deleteTrigger(trigger);
  });
}

/**
 * Called by the clock. Starts one monitoring run. A failure throws so that
 * Google emails the person who installed the clock.
 */
function tick() {
  const code = startWorkflow_(MONITOR_WORKFLOW, { action: 'poll' });
  if (code !== 204) throw new Error('Could not start the alert monitor: ' + describe_(code));
}

// --- Commands ---------------------------------------------------------------

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
  const code = startWorkflow_(REQUEST_WORKFLOW, { command: command });
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

function reply_(text) {
  return { hostAppDataAction: { chatDataAction: { createMessageAction: { message: { text: text } } } } };
}

// --- GitHub -----------------------------------------------------------------

/** Returns the HTTP status from GitHub (204 means started), or 0 if no token is stored. */
function startWorkflow_(workflow, inputs) {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) return 0;
  const url = 'https://api.github.com/repos/' + REPO + '/actions/workflows/' + workflow + '/dispatches';
  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({ ref: BRANCH, inputs: inputs }),
    muteHttpExceptions: true,
  });
  const code = response.getResponseCode();
  if (code !== 204) console.error('GitHub dispatch failed: ' + code + ' ' + response.getContentText());
  return code;
}

function describe_(code) {
  if (code === 0) return 'no GITHUB_TOKEN script property found. Add it under Project Settings.';
  if (code === 401 || code === 403) {
    return 'GitHub answered ' + code + '. The token is wrong, expired, or lacks Actions read and write access.';
  }
  if (code === 404) {
    return 'GitHub answered 404. The repository, workflow or branch name is wrong, or the token cannot see the repository.';
  }
  return 'GitHub answered ' + code + '.';
}

/**
 * Run this once from the editor (select testConnection, then Run). It asks for
 * permission to contact GitHub, then requests a real update, so a conditions
 * update should appear in the alerts space within about 30 seconds.
 */
function testConnection() {
  const code = startWorkflow_(REQUEST_WORKFLOW, { command: 'update' });
  console.log(code === 204 ? 'Success: GitHub accepted the request. Watch the alerts space.' : 'Failed: ' + describe_(code));
}
