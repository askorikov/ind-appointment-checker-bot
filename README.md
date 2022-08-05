# IND appointment checker bot for Telegram
This bot allows user to set up one or several jobs that will periodically check
for appointments of a specified type at a specified IND location (migration
authority of the Netherlands). It was inspired by https://github.com/Iaotle/IND-Appointment-Check.

## Usage
* `/add` - Add a new job to watch for an appointment.
* `/cancel` - Cancel the current dialogue.
* `/list` - List all current jobs.
* `/clear` - Remove all current jobs from the queue.
* `/help` - Display help message.

## Setup
Currently the bot is running as `@IndAppointmentCheckerBot`. It can be
self-hosted after creating your own bot with `@BotFather` (see
https://core.telegram.org/bots) in one of the following ways:

### Locally
1. `pip install -r requirements.txt`
2. Set up your bot token as `TELEGRAM_BOT_TOKEN` environment variable.
3. `python bot.py`

### On Heroku
1. Create your Heroku app:
    ```
    heroku create
    ```
   or add a Heroku remote for an existing app:
    ```
    heroku git:remote -a <your-existing-app>
    ```
2. Set up your bot token as `TELEGRAM_BOT_TOKEN` environment variable on Heroku:
    ```
    heroku config:set TELEGRAM_BOT_TOKEN=<your token>
    ```
3. Activate Heroku [Dyno Metadata API](https://devcenter.heroku.com/articles/dyno-metadata):
    ```
    heroku labs:enable runtime-dyno-metadata
    ```
   or set up `HEROKU_APP_NAME` environment variable.
4. Deploy the code:
    ```
    git push heroku main
    ```
5. The bot will prevent (free-tier) dynos from sleeping by pinging itself
   periodically if any job is running. This may exhaust your free hours limit
   if jobs are running for too long. This can be disabled by passing
   `--no-keep-awake` flag to the bot by changing the `Procfile`.
