# Google Apps Script helper: the clock, and `/update` and `/help`

A small Google Apps Script project does two jobs for the alert monitor.

**Part A: the clock (needed for the monitor to be dependable).** Every 5 minutes the script starts a monitoring run on GitHub. GitHub has its own scheduler, but in this repository's first day it started the 5-minute job only twice in 21 hours. Apps Script timers are far steadier, so the script is the clock and GitHub's scheduler is only a backup.

**Part B: commands (optional).** The script lets people in the alerts space type:

- `/update` to post current alerts, the campus forecast and road conditions.
- `/help` to post the guide to reading the alerts.

The monitor posts through a webhook, which is one-way: it cannot see what people type. That is why commands need a Chat app. Commands start with `/` rather than `#` because in a space, a Chat app is only told about slash commands and @mentions, not ordinary messages.

In both parts the script only starts a workflow in this repository; the workflow does the work and posts to the space.

Part A takes about 10 minutes and needs only a Google account in your Workspace and a GitHub token. Part B takes about 15 more and needs someone who can create a Google Cloud project in your Workspace domain. Part B follows Google's [Apps Script Chat app quickstart](https://developers.google.com/workspace/chat/quickstart/apps-script-app) and [commands guide](https://developers.google.com/workspace/chat/commands).

The code was written against Google's documentation in November 2026 and its logic was checked outside Google, but it has not run inside Apps Script. Treat the tests in the steps below as the real test.

## Part A: the clock

### 1. Create a GitHub token

1. On GitHub: your profile picture → *Settings* → *Developer settings* → *Personal access tokens* → *Fine-grained tokens* → *Generate new token*.
2. Name it "Safety Alerts Apps Script". Set an expiration and put a reminder on your calendar: **when the token expires, the clock stops and alerts stop with it.**
3. *Repository access* → *Only select repositories* → `CDS-safety-alerts`.
4. *Permissions* → *Repository permissions* → **Actions: Read and write**. Nothing else.
5. Generate the token and copy it. Treat it like a password.

### 2. Create the Apps Script project

1. Open the Apps Script [Getting Started page](https://script.google.com/home/start), and under *Google Workspace add-on starters* click the **Chat app** template. Starting from the template gives the project the manifest Part B needs.
2. Rename the project "Safety Alerts helper".
3. Replace everything in `Code.gs` with the contents of [`Code.gs`](Code.gs) in this folder.
4. *Project Settings* (gear icon) → *Script Properties* → *Add script property*: name `GITHUB_TOKEN`, value the token from step 1.
5. Still in *Project Settings*, tick *Show "appsscript.json" manifest file in editor*, then open `appsscript.json`. If it contains an `oauthScopes` list, add these two lines to the list; if there is no such list, leave the file alone.

   ```json
   "https://www.googleapis.com/auth/script.external_request",
   "https://www.googleapis.com/auth/script.scriptapp"
   ```

### 3. Test it, then start the clock

1. In the editor, choose the `testConnection` function and click *Run*. Approve the permission prompt. The log should say GitHub accepted the request, and a conditions update should appear in the alerts space within about 30 seconds. Fix any error here before going on.
2. Choose the `installClock` function and click *Run*. Approve the prompt if asked.
3. Check it is working: after 10 minutes, the *Triggers* page (clock icon) should list `tick` with recent runs and no failures, and *Actions* → *Safety alerts* in this repository should show a new run about every 5 minutes.

The clock belongs to whoever ran `installClock`. If that person's account is removed, someone else must run `installClock` in the project. Google emails that person if `tick` starts failing.

To stop the clock, run `removeClock`.

## Part B: `/update` and `/help`

### 4. Create the Google Cloud project

1. In the [Google Cloud console](https://console.cloud.google.com/), create a project named "Safety Alerts".
2. Enable the **Google Chat API** for the project.
3. Go to *Google Auth platform* → *Branding* and complete the consent screen: app name "Safety Alerts", your support email, audience **Internal**.

### 5. Configure the Chat app

1. In the Apps Script project: *Deploy* → *Test deployments* → copy the **Head deployment ID**.
2. In the Cloud console: *APIs & Services* → *Enabled APIs & Services* → *Google Chat API* → *Configuration*.
3. **App name:** Safety Alerts. **Description:** Weather and road updates on request. **Avatar URL:** any square image URL.
4. **Functionality:** tick *Join spaces and group conversations*.
5. **Connection settings:** *Apps Script project*, and paste the Head deployment ID. Under *Triggers*, the function names should be `onAppCommand` for app commands, `onMessage` for messages and `onAddedToSpace` for added to space; type them in if a field is empty.
6. **Commands** → *Add a command*, twice:

   | Command ID | Type | Name | Description |
   |---|---|---|---|
   | 1 | Slash command | `/update` | Post current weather and road conditions |
   | 2 | Slash command | `/help` | Explain how to read the alerts |

   The IDs must match `UPDATE_COMMAND_ID` and `HELP_COMMAND_ID` at the top of `Code.gs`.
7. **Visibility:** *Specific people and groups in your domain*, and list the people in the alerts space (or a Google group).
8. *Save*.

### 6. Add it to the space and test

1. In the alerts space: space menu → *Apps & integrations* → *Add apps* → search for "Safety Alerts" → add.
2. Type `/update` and send. The app should answer "On it…" right away, and the conditions update should follow.
3. Type `/help` and check the guide appears.
4. In this repository, set `commands = true` under `[chat]` in [`config.toml`](../config.toml) so the guide mentions the commands.

Each person may be asked to approve the app the first time they use a command.

## If something goes wrong

- **`tick` shows failures on the Triggers page:** open the failed execution to see what GitHub answered.
- **"GitHub answered 401" or "403":** the token is wrong, expired, or lacks *Actions: Read and write*.
- **"GitHub answered 404":** `REPO`, `BRANCH` or a workflow name at the top of `Code.gs` does not match the repository, or the token was not given access to it.
- **The app does not appear when adding apps:** check *Visibility* in step 5 includes that person.
- **"Something went wrong" in Chat:** open the Apps Script project → *Executions* to see the error.
- **"On it…" but nothing posts:** look at *Actions* → *On request* in this repository for the failed run.

The reply to a command always posts in the alerts space, even if someone uses the command in a direct message with the app.
