# `/update` and `/help` in Google Chat

This adds two commands to the alerts space:

- `/update` posts current alerts, the campus forecast and road conditions.
- `/help` posts the guide to reading the alerts.

The alert monitor posts through a webhook, which is one-way: it cannot see what people type. So the commands need a small Google Chat app. The app does one thing: when someone uses a command, it starts the **On request** workflow in this repository, and that workflow posts the reply about 20 to 30 seconds later.

Commands start with `/` rather than `#` because in a space, a Chat app is only told about slash commands and @mentions, not ordinary messages.

Setup takes about 20 minutes and needs someone who can create a Google Cloud project in your Workspace domain. It follows Google's [Apps Script Chat app quickstart](https://developers.google.com/workspace/chat/quickstart/apps-script-app) and [commands guide](https://developers.google.com/workspace/chat/commands); the code was written against those pages in November 2026 but has not been run, so treat the first test as the real test.

## 1. Create a GitHub token

The app needs permission to start one workflow.

1. On GitHub: your profile picture → *Settings* → *Developer settings* → *Personal access tokens* → *Fine-grained tokens* → *Generate new token*.
2. Name it "Safety Alerts chat commands". Set an expiration and put a reminder on your calendar; commands stop working when it expires.
3. *Repository access* → *Only select repositories* → `CDS-safety-alerts`.
4. *Permissions* → *Repository permissions* → **Actions: Read and write**. Nothing else.
5. Generate the token and copy it. Treat it like a password.

## 2. Create the Google Cloud project

1. In the [Google Cloud console](https://console.cloud.google.com/), create a project named "Safety Alerts".
2. Enable the **Google Chat API** for the project.
3. Go to *Google Auth platform* → *Branding* and complete the consent screen: app name "Safety Alerts", your support email, audience **Internal**.

## 3. Create the Apps Script project

1. Open the Apps Script [Getting Started page](https://script.google.com/home/start), and under *Google Workspace add-on starters* click the **Chat app** template. Starting from the template gives the project the right manifest.
2. Rename the project "Safety Alerts commands".
3. Replace everything in `Code.gs` with the contents of [`Code.gs`](Code.gs) in this folder.
4. *Project Settings* (gear icon) → *Script Properties* → *Add script property*: name `GITHUB_TOKEN`, value the token from step 1.
5. Still in *Project Settings*, tick *Show "appsscript.json" manifest file in editor*, then open `appsscript.json`. If it contains an `oauthScopes` list, add this line to the list; if there is no such list, leave the file alone.

   ```json
   "https://www.googleapis.com/auth/script.external_request"
   ```

6. In the editor, choose the `testConnection` function and click *Run*. Approve the permission prompt. The log should say GitHub accepted the request, and a conditions update should appear in the alerts space within about 30 seconds. Fix any error here before going on.
7. *Deploy* → *Test deployments* → copy the **Head deployment ID**.

## 4. Configure the Chat app

In the Cloud console: *APIs & Services* → *Enabled APIs & Services* → *Google Chat API* → *Configuration*.

1. **App name:** Safety Alerts. **Description:** Weather and road updates on request. **Avatar URL:** any square image URL.
2. **Functionality:** tick *Join spaces and group conversations*.
3. **Connection settings:** *Apps Script project*, and paste the Head deployment ID. Under *Triggers*, the function names should be `onAppCommand` for app commands, `onMessage` for messages and `onAddedToSpace` for added to space; type them in if a field is empty.
4. **Commands** → *Add a command*, twice:

   | Command ID | Type | Name | Description |
   |---|---|---|---|
   | 1 | Slash command | `/update` | Post current weather and road conditions |
   | 2 | Slash command | `/help` | Explain how to read the alerts |

   The IDs must match `UPDATE_COMMAND_ID` and `HELP_COMMAND_ID` at the top of `Code.gs`.
5. **Visibility:** *Specific people and groups in your domain*, and list the people in the alerts space (or a Google group).
6. *Save*.

## 5. Add it to the space and test

1. In the alerts space: space menu → *Apps & integrations* → *Add apps* → search for "Safety Alerts" → add.
2. Type `/update` and send. The app should answer "On it…" right away, and the conditions update should follow.
3. Type `/help` and check the guide appears.
4. In this repository, set `commands = true` under `[chat]` in [`config.toml`](../config.toml) so the guide mentions the commands.

Each person may be asked to approve the app the first time they use a command.

## If something goes wrong

- **The app does not appear when adding apps:** check *Visibility* in step 4 includes that person.
- **"Something went wrong" in Chat:** open the Apps Script project → *Executions* to see the error.
- **"GitHub answered 401" or "403":** the token is wrong, expired, or lacks *Actions: Read and write*.
- **"GitHub answered 404":** `REPO`, `WORKFLOW` or `BRANCH` at the top of `Code.gs` does not match the repository, or the token was not given access to it.
- **"On it…" but nothing posts:** look at *Actions* → *On request* in this repository for the failed run.

The reply always posts in the alerts space, even if someone uses the command in a direct message with the app.
