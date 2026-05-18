# GitHub Agent Account Runbook

Use this runbook when creating a GitHub account for a new Witwave self-team agent, such as `mira-agent-witwave`. The
result is a verified GitHub user, member/owner access in the Witwave organization, a profile avatar, and an encrypted
`agent.sops.env` file ready for `ww agent create`.

## Placeholders

| Placeholder    | Meaning                         | Example                 |
| -------------- | ------------------------------- | ----------------------- |
| `<agent-name>` | Lowercase agent name            | `mira`                  |
| `<Agent Name>` | Title-cased agent name          | `Mira`                  |
| Agent email    | `<agent-name>-agent@witwave.ai` | `mira-agent@witwave.ai` |
| GitHub user    | `<agent-name>-agent-witwave`    | `mira-agent-witwave`    |
| Profile name   | `<Agent Name> Witwave`          | `Mira Witwave`          |

## Prerequisites

- Access to the agent's `witwave.ai` mailbox.
- Access to the "standard password" from the approved secure store.
- Authy available for two-factor authentication.
- A separate browser/session logged in as an owner of the Witwave GitHub organization.
- Local SOPS/age setup working for this repo.
- An approved secure place, such as 1Password, for recovery codes and PAT storage.

## Security Rules

- Do not store passwords, recovery codes, or plaintext GitHub tokens in the repo.
- Store GitHub recovery codes in the agent's secure account item before clicking GitHub's confirmation button.
- The initial fine-grained PAT may be broad during bootstrap, but treat that as temporary and tighten it once the
  agent's actual GitHub permissions are understood.
- Do not commit `agent.sops.env` unless SOPS encrypted both `GITHUB_TOKEN` and `GITHUB_USER`.

## Procedure

### Create The Account

1. Navigate to <https://github.com>.
2. Click **Sign up** in the upper-right corner.
3. In the email field, enter `<agent-name>-agent@witwave.ai`.
4. In the password field, use the "standard password".
5. In the username field, enter `<agent-name>-agent-witwave`.
6. Click **Create account**.
7. Use the defaults for the remaining GitHub onboarding fields unless the agent has a specific setup need.

### Verify Email And Log In

1. Open the agent's `witwave.ai` mailbox.
2. Open GitHub's **Confirm your email address** message.
3. Supply the **launch code** from the verification email.
4. When GitHub redirects to the login screen, log in with the agent email address and the password supplied earlier.

### Enable Two-Factor Authentication

1. Select the user navigation icon in the upper-right corner.
2. Choose **Settings**.
3. Choose **Password and authentication**.
4. Enable two-factor authentication using Authy.
5. After GitHub confirms the Authy code, download the one-time recovery codes.
6. Store the recovery codes in the approved secure location for the agent account, such as the agent's 1Password item.
7. Choose **I have saved my recovery codes**.
8. Choose **Done** to close out two-factor authentication setup.

### Complete The Public Profile

1. Select the user navigation icon in the upper-right corner again.
2. Choose **Profile**.
3. Set the profile name to `<Agent Name> Witwave`.
4. Set the public email to `<agent-name>-agent@witwave.ai`.
5. Set the pronouns for the agent. Example for Mira: `she/her`.
6. Navigate to <https://www.dicebear.com/> and choose an avatar for the agent.
7. Record the DiceBear style and seed so the avatar can be reproduced later.
8. Download the chosen avatar into `.agents/self/<agent-name>/assets/avatar.png`.
9. Update the GitHub public profile with the new avatar image, public email address, website, and pronouns.

### Join The Witwave Organization

1. In a separate browser/session logged in as the owner of the Witwave GitHub organization, open the organization
   **People** tab.
2. Invite `<agent-name>-agent-witwave` as an owner.
3. In the agent's mailbox, open the organization invitation email.
4. Click through the invitation link to join the Witwave organization.

### Create And Store The GitHub Token

1. As the agent account, select the profile icon.
2. Choose **Settings**.
3. Choose **Developer settings**.
4. Create a new fine-grained personal access token.
5. Set the token to expire after 90 days.
6. For the initial setup, grant full privileges.
7. Store the token in the approved secure location.
8. Update `.agents/self/<agent-name>/agent.sops.env` with:

   ```dotenv
   GITHUB_TOKEN=<token>
   GITHUB_USER=<agent-name>-agent-witwave
   ```

9. Encrypt the file with SOPS before committing:

   ```bash
   chmod 600 .agents/self/<agent-name>/agent.sops.env
   mise exec -- sops --encrypt --in-place .agents/self/<agent-name>/agent.sops.env
   ```

10. Verify the file shape without printing secret values:

    ```bash
    grep -q '^GITHUB_TOKEN=ENC\\[' .agents/self/<agent-name>/agent.sops.env
    grep -q '^GITHUB_USER=ENC\\[' .agents/self/<agent-name>/agent.sops.env
    ```

11. Verify decrypt/auth in memory:

    ```bash
    mise exec -- scripts/sops-exec-env.py .agents/self/<agent-name>/agent.sops.env -- \
      sh -lc 'test -n "$GITHUB_TOKEN" && test "$GITHUB_USER" = "<agent-name>-agent-witwave"'

    mise exec -- scripts/sops-exec-env.py .agents/self/<agent-name>/agent.sops.env -- \
      sh -lc 'GH_TOKEN="$GITHUB_TOKEN" gh api user --jq .login'
    ```

    The second command should print `<agent-name>-agent-witwave`.
