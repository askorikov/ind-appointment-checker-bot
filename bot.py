import asyncio
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime
from enum import Enum
from typing import Dict
from urllib.error import URLError, HTTPError

from telegram import Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (ApplicationBuilder, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.WARNING
)
logger = logging.getLogger(__name__)


HEROKU = True if '--heroku' in sys.argv else False
NO_KEEP_AWAKE = True if '--no-keep-awake' in sys.argv else False
KEEP_AWAKE_INTERVAL = 29*60
LOCATION_MAPPING = {
    'Amsterdam': 'AM',
    'Den Haag': 'DH',
    'Zwolle': 'ZW',
    'Den Bosch': 'DB'
}
APPOINTMENT_TYPE_MAPPING = {
    'Collecting residence document': 'DOC',
    'Biometric data': 'BIO',
    'Residence endorsement sticker': 'VAA',
    'Return visa': 'TKV'
}
APPOINTMENT_CHECK_INTERVAL = 10
HELP_STRING = ('/add - Add a new job to watch for an appointment.\n'
               '/cancel - Cancel the current dialogue.\n'
               '/list - List all current jobs.\n'
               '/clear - Remove all current jobs from the queue.\n'
               '/help - Display help message.')


class ResponseType(Enum):
    LOCATION = 1
    APPOINTMENT_TYPE = 2
    NUM_PEOPLE = 3
    BEFORE_DATE = 4


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        'This bot allows user to set up one or several jobs that will '
        'periodically check for appointments of a specified type at a specified '
        'location of IND (migration authority of the Netherlands).\n\n'
        + HELP_STRING
    )


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_STRING)


async def start_dialogue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ResponseType:
    reply_markup = ReplyKeyboardMarkup([[x] for x in LOCATION_MAPPING],
                                       resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text('Choose location:', reply_markup=reply_markup)
    return ResponseType.LOCATION


async def get_appointment_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ResponseType:
    context.user_data['location'] = update.message.text
    reply_markup = ReplyKeyboardMarkup([[x] for x in APPOINTMENT_TYPE_MAPPING],
                                       resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text('Choose appointment type:', reply_markup=reply_markup)
    return ResponseType.APPOINTMENT_TYPE


async def get_num_people(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ResponseType:
    context.user_data['appointment_type'] = update.message.text
    reply_markup = ReplyKeyboardMarkup([[str(i + 1)] for i in range(6)],
                                        resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text('Choose number of people:', reply_markup=reply_markup)
    return ResponseType.NUM_PEOPLE


async def get_before_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ResponseType:
    context.user_data['num_people'] = int(update.message.text)
    await update.message.reply_text(
        'Date before which to search for an appointment (dd-mm-yyyy):',
        reply_markup=ReplyKeyboardRemove()
    )
    return ResponseType.BEFORE_DATE


async def finish_dialogue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ResponseType:
    try:
        context.user_data['before_date'] = datetime.strptime(update.message.text, '%d-%m-%Y')
    except ValueError:
        logger.warning(f'{update.message.text} could not be parsed in dd-mm-yyyy '
                        'format despite passing the regex filter before.')
        return

    job_name = (f'{context.user_data["location"]}, '
                f'{context.user_data["num_people"]} x {context.user_data["appointment_type"]}, '
                f'before {context.user_data["before_date"]:%d-%m-%Y}')
    url = get_ind_api_url(context.user_data)
    context.job_queue.run_repeating(check_appointment,
                                    interval=APPOINTMENT_CHECK_INTERVAL,
                                    first=0.2,  # run after a short grace time
                                    last=context.user_data['before_date'] - datetime.now(),
                                    chat_id=update.effective_chat.id,
                                    data={
                                        'url': url,
                                        'before_date': context.user_data['before_date']
                                    },
                                    name=job_name)

    # Prevent from sleeping on Heroku free tier by pinging the app periodically
    if (HEROKU and not NO_KEEP_AWAKE
        and not wake_up in [x.callback for x in context.job_queue.jobs()]):
        context.job_queue.run_once(wake_up, when=KEEP_AWAKE_INTERVAL)

    await update.message.reply_text('Appointment monitor started. You will get '
                                    'a notification if an appointment is found.')
    return ConversationHandler.END


async def cancel_dialogue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ResponseType:
    await update.message.reply_text(
        'Scheduling cancelled.', reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


async def list_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    jobs = [x for x in context.job_queue.jobs() if x.chat_id == update.effective_chat.id]
    if len(jobs) > 0:
        response = ['Currently looking for:']
        for i, job in enumerate(jobs):
            response.append(f'{i+1}) {job.name}')
        await update.message.reply_text('\n'.join(response))
    else:
        await update.message.reply_text('No jobs scheduled at the moment.')


async def clear_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    jobs = [x for x in context.job_queue.jobs() if x.chat_id == update.effective_chat.id]
    if len(jobs) > 0:
        for job in context.job_queue.jobs():
            job.schedule_removal()
        await update.message.reply_text(f'{len(jobs)} jobs were removed from the queue.')
    else:
        await update.message.reply_text('No jobs scheduled at the moment. Nothing to clear.')


def get_ind_api_url(user_data: Dict) -> str:
    location = LOCATION_MAPPING[user_data['location']]
    appointment_type = APPOINTMENT_TYPE_MAPPING[user_data['appointment_type']]
    num_people = user_data['num_people']
    return (f'https://oap.ind.nl/oap/api/desks/{location}/slots/'
            f'?productKey={appointment_type}&persons={num_people}')


async def check_appointment(context: ContextTypes.DEFAULT_TYPE) -> None:
    async def stop_job(message):
        await context.bot.send_message(context.job.chat_id, message)
        context.job.schedule_removal()

    url = context.job.data['url']
    try:
        with urllib.request.urlopen(url) as web_content:
            response = web_content.read()
    except URLError:
        logger.exception('Cannot reach IND API.')
        await stop_job(message='Cannot reach IND API. Job cancelled.')
        return

    try:
        response = response[6:]  # Some closing brackets are returned in the start of the response
        response = json.loads(response)
        response = response['data']
        if not response:
            return
        earliest_appointment_info = response[0]
        earliest_date = earliest_appointment_info['date']
        time = earliest_appointment_info['startTime']
        earliest_date = datetime.strptime(earliest_date + ' ' + time, '%Y-%m-%d %H:%M')
    except:
        logger.exception('IND appears to have changed their API.')
        await stop_job(message='IND appears to have changed their API. Job cancelled.')
        return

    before_date = context.job.data['before_date']
    if (earliest_date < before_date):
        await stop_job(message=f'Appointment found on {earliest_date:%d-%m-%Y %H:%M}')


async def wake_up(context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.job_queue.jobs()) > 0:
        heroku_app_name = os.environ['HEROKU_APP_NAME']
        # Run in executor to not block the asyncio loop that runs the bot
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,  # default executor
                urllib.request.urlopen,
                f'https://{heroku_app_name}.herokuapp.com/'
            )
        except HTTPError:
            pass  # expecting error 404
        context.job_queue.run_once(wake_up, when=KEEP_AWAKE_INTERVAL)


class DateFilter(filters.MessageFilter):
    def filter(self, message: Message) -> bool:
        try:
            datetime.strptime(message.text, '%d-%m-%Y')
            return True
        except (ValueError, TypeError):
            return False


def main() -> None:
    token = os.environ['TELEGRAM_BOT_TOKEN']
    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help))
    conversation_handler = ConversationHandler(
        entry_points=[CommandHandler('add', start_dialogue)],
        states={
            ResponseType.LOCATION: [
                MessageHandler(filters.Text(LOCATION_MAPPING), get_appointment_type)
            ],
            ResponseType.APPOINTMENT_TYPE: [
                MessageHandler(filters.Text(APPOINTMENT_TYPE_MAPPING), get_num_people)
            ],
            ResponseType.NUM_PEOPLE: [
                MessageHandler(filters.Regex('^[1-6]$'), get_before_date)
            ],
            ResponseType.BEFORE_DATE: [
                MessageHandler(DateFilter(), finish_dialogue)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_dialogue)],
    )
    application.add_handler(conversation_handler)
    application.add_handler(CommandHandler('list', list_jobs))
    application.add_handler(CommandHandler('clear', clear_jobs))

    if HEROKU:
        port = int(os.environ.get('PORT', '8443'))
        heroku_app_name = os.environ['HEROKU_APP_NAME']
        application.run_webhook(
            listen='0.0.0.0',
            port=port,
            url_path=token,
            webhook_url=f'https://{heroku_app_name}.herokuapp.com/{token}'
        )
    else:
        application.run_polling()


if __name__ == '__main__':
    main()
