import asyncio
import os
from datetime import datetime, timedelta
from typing import Dict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Константы Pomodoro
POMODORO_DURATION = 25 * 60  # 25 минут в секундах
SHORT_BREAK_DURATION = 5 * 60  # 5 минут в секундах
LONG_BREAK_DURATION = 15 * 60  # 15 минут в секундах

# Инициализация бота и диспетчера
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Хранилище активных таймеров и статистики пользователей
active_timers: Dict[int, asyncio.Task] = {}
user_stats: Dict[int, Dict[str, int]] = {}  # {user_id: {"pomodoros": 0, "short_breaks": 0, "long_breaks": 0}}
user_intervals: Dict[int, Dict[str, int]] = {}  # {user_id: {"pomodoro": 25, "short_break": 5, "long_break": 15}}
active_cycles: Dict[int, asyncio.Task] = {}  # Активные циклы Pomodoro


class PomodoroStates(StatesGroup):
    waiting_for_pomodoro_interval = State()
    waiting_for_short_break_interval = State()
    waiting_for_long_break_interval = State()


def get_user_stats(user_id: int) -> Dict[str, int]:
    """Получить статистику пользователя"""
    if user_id not in user_stats:
        user_stats[user_id] = {"pomodoros": 0, "short_breaks": 0, "long_breaks": 0}
    return user_stats[user_id]


def get_user_intervals(user_id: int) -> Dict[str, int]:
    """Получить интервалы пользователя"""
    if user_id not in user_intervals:
        user_intervals[user_id] = {
            "pomodoro": POMODORO_DURATION,
            "short_break": SHORT_BREAK_DURATION,
            "long_break": LONG_BREAK_DURATION
        }
    return user_intervals[user_id]


def format_time(seconds: int) -> str:
    """Форматировать время в формат ММ:СС или просто секунды если меньше минуты"""
    if seconds < 60:
        return f"{seconds} сек"
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def get_main_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    """Создать основную клавиатуру"""
    if user_id:
        intervals = get_user_intervals(user_id)
        pomodoro_min = intervals['pomodoro'] // 60
        short_min = intervals['short_break'] // 60
        long_min = intervals['long_break'] // 60
        pomodoro_text = f"🍅 Настроить Pomodoro ({pomodoro_min} мин)"
        short_text = f"☕ Настроить короткий перерыв ({short_min} мин)"
        long_text = f"🌴 Настроить длинный перерыв ({long_min} мин)"
    else:
        pomodoro_text = "🍅 Настроить Pomodoro"
        short_text = "☕ Настроить короткий перерыв"
        long_text = "🌴 Настроить длинный перерыв"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Запустить полный цикл Pomodoro", callback_data="start_full_cycle")],
        [InlineKeyboardButton(text=pomodoro_text, callback_data="set_pomodoro_interval")],
        [InlineKeyboardButton(text=short_text, callback_data="set_short_break_interval")],
        [InlineKeyboardButton(text=long_text, callback_data="set_long_break_interval")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="show_stats")],
        [InlineKeyboardButton(text="⏹️ Остановить таймер/цикл", callback_data="stop_timer")]
    ])
    return keyboard


def get_settings_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для настроек"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    return keyboard


def get_stop_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой остановки для уведомлений"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹️ Остановить цикл", callback_data="stop_timer")]
    ])
    return keyboard


async def send_timer_update(chat_id: int, message_id: int, remaining_seconds: int, timer_type: str):
    """Отправить обновление таймера"""
    time_str = format_time(remaining_seconds)
    emoji = "🍅" if timer_type == "pomodoro" else "☕" if timer_type == "short_break" else "🌴"
    type_name = "Pomodoro" if timer_type == "pomodoro" else "Короткий перерыв" if timer_type == "short_break" else "Длинный перерыв"
    
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"{emoji} {type_name}\n\n⏱ Осталось времени: {time_str}",
            reply_markup=get_stop_keyboard()
        )
    except Exception:
        pass  # Игнорируем ошибки редактирования (например, если сообщение уже было изменено)


async def run_timer(chat_id: int, message_id: int, duration: int, timer_type: str, user_id: int, is_cycle: bool = False, notification_message_id: int = None):
    """Запустить таймер"""
    remaining = duration
    update_interval = 1  # Обновлять каждую секунду для отображения обратного отсчета
    
    # Обновляем только уведомление, если оно есть, иначе основное сообщение
    target_message_id = notification_message_id if notification_message_id else message_id
    
    # Отправляем начальное обновление таймера
    await send_timer_update(chat_id, target_message_id, remaining, timer_type)
    
    while remaining > 0:
        await asyncio.sleep(min(update_interval, remaining))
        remaining -= min(update_interval, remaining)
        
        if remaining > 0:
            await send_timer_update(chat_id, target_message_id, remaining, timer_type)
    
    # Таймер завершен
    emoji = "🍅" if timer_type == "pomodoro" else "☕" if timer_type == "short_break" else "🌴"
    type_name = "Pomodoro" if timer_type == "pomodoro" else "Короткий перерыв" if timer_type == "short_break" else "Длинный перерыв"
    
    # Обновляем статистику
    stats = get_user_stats(user_id)
    if timer_type == "pomodoro":
        stats["pomodoros"] += 1
    elif timer_type == "short_break":
        stats["short_breaks"] += 1
    else:
        stats["long_breaks"] += 1
    
    # Отправляем уведомление о завершении
    completion_text = f"✅ {type_name} завершен!\n\n"
    if timer_type == "pomodoro":
        completion_text += f"🎉 Поздравляем! Вы завершили {stats['pomodoros']} сессий Pomodoro!"
        if stats["pomodoros"] % 4 == 0:
            completion_text += "\n\n💡 Рекомендуется сделать длинный перерыв!"
    else:
        completion_text += "💪 Готовы продолжить работу?"
    
    if not is_cycle:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=completion_text,
                reply_markup=get_main_keyboard(user_id)
            )
        except Exception:
            await bot.send_message(
                chat_id=chat_id,
                text=completion_text,
                reply_markup=get_main_keyboard(user_id)
            )
        
        # Удаляем таймер из активных
        if user_id in active_timers:
            del active_timers[user_id]
    
    return completion_text


async def run_full_cycle(chat_id: int, message_id: int, user_id: int):
    """Запустить полный цикл Pomodoro (4 pomodoro + перерывы)"""
    intervals = get_user_intervals(user_id)
    pomodoro_count = 0
    is_first_pomodoro = True
    
    try:
        # Уведомление о начале цикла
        first_notification = await bot.send_message(
            chat_id=chat_id,
            text=f"🔔 **ЦИКЛ ПОМОДОРО ЗАПУЩЕН!**\n\n🍅 Первый Pomodoro начинается!\n\n⏱ Осталось времени: {format_time(intervals['pomodoro'])}\n\n💪 Готовы работать продуктивно?",
            reply_markup=get_stop_keyboard()
        )
        
        while user_id in active_cycles:  # Продолжаем пока цикл активен
            pomodoro_count += 1
            
            # Уведомление о начале Pomodoro (кроме первого)
            notification_msg = None
            if not is_first_pomodoro:
                notification_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=f"🔔 **НАЧАЛО РАБОТЫ!**\n\n🍅 Pomodoro #{pomodoro_count} начинается!\n\n⏱ Осталось времени: {format_time(intervals['pomodoro'])}\n\n💪 Время сосредоточиться и работать продуктивно!",
                    reply_markup=get_stop_keyboard()
                )
            # Для первого Pomodoro используем первое уведомление, для остальных - новое
            if pomodoro_count == 1:
                notification_id = first_notification.message_id
            elif notification_msg:
                notification_id = notification_msg.message_id
            else:
                notification_id = None
            
            is_first_pomodoro = False
            
            await run_timer(chat_id, message_id, intervals['pomodoro'], "pomodoro", user_id, is_cycle=True, notification_message_id=notification_id)
            
            # Проверяем, не остановлен ли цикл
            if user_id not in active_cycles:
                break
            
            # Перерыв (каждый 4-й - длинный, остальные - короткие)
            if pomodoro_count % 4 == 0:
                break_type = "long_break"
                break_duration = intervals['long_break']
                break_emoji = "🌴"
                break_name = "Длинный перерыв"
            else:
                break_type = "short_break"
                break_duration = intervals['short_break']
                break_emoji = "☕"
                break_name = "Короткий перерыв"
            
            # Уведомление о начале перерыва
            notification = await bot.send_message(
                chat_id=chat_id,
                text=f"🔔 **ВРЕМЯ ОТДЫХАТЬ!**\n\n{break_emoji} {break_name} после Pomodoro #{pomodoro_count}\n\n⏱ Осталось времени: {format_time(break_duration)}\n\n😌 Расслабьтесь и восстановите силы!",
                reply_markup=get_stop_keyboard()
            )
            
            # Обновляем только уведомление с таймером, главное сообщение не трогаем
            await run_timer(chat_id, message_id, break_duration, break_type, user_id, is_cycle=True, notification_message_id=notification.message_id)
            
            # Проверяем, не остановлен ли цикл
            if user_id not in active_cycles:
                break
        
        # Цикл завершен или остановлен
        if user_id in active_cycles:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"⏹️ Цикл Pomodoro остановлен.\n\n✅ Завершено Pomodoro: {pomodoro_count}",
                reply_markup=get_main_keyboard(user_id)
            )
            del active_cycles[user_id]
    except asyncio.CancelledError:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"⏹️ Цикл Pomodoro остановлен.\n\n✅ Завершено Pomodoro: {pomodoro_count}",
            reply_markup=get_main_keyboard(user_id)
        )
        if user_id in active_cycles:
            del active_cycles[user_id]


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    intervals = get_user_intervals(user_id)
    pomodoro_min = intervals['pomodoro'] // 60
    short_min = intervals['short_break'] // 60
    long_min = intervals['long_break'] // 60
    welcome_text = (
        "🍅 Добро пожаловать в Pomodoro бота!\n\n"
        "Техника Pomodoro поможет вам повысить продуктивность:\n"
        f"• 🍅 Pomodoro: {pomodoro_min} минут\n"
        f"• ☕ Короткий перерыв: {short_min} минут\n"
        f"• 🌴 Длинный перерыв: {long_min} минут\n\n"
        "Используйте кнопки ниже для управления таймерами.\n"
        "Вы можете настроить интервалы по своему желанию!"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(user_id))


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    help_text = (
        "📖 Помощь по использованию бота:\n\n"
        "Команды:\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать эту справку\n"
        "/stats - Показать статистику\n\n"
        "Кнопки:\n"
        "🔄 Запустить полный цикл - запустить бесконечный цикл Pomodoro\n"
        "🍅 Настроить Pomodoro - изменить длительность Pomodoro\n"
        "☕ Настроить короткий перерыв - изменить длительность короткого перерыва\n"
        "🌴 Настроить длинный перерыв - изменить длительность длинного перерыва\n"
        "📊 Статистика - посмотреть вашу статистику\n"
        "⏹️ Остановить таймер/цикл - остановить текущий таймер или цикл\n\n"
        "💡 Совет: После каждых 4 Pomodoro делается длинный перерыв!"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard(message.from_user.id))


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Обработчик команды /stats"""
    user_id = message.from_user.id
    stats = get_user_stats(user_id)
    intervals = get_user_intervals(user_id)
    pomodoro_min = intervals['pomodoro'] // 60
    short_min = intervals['short_break'] // 60
    long_min = intervals['long_break'] // 60
    stats_text = (
        f"📊 Ваша статистика:\n\n"
        f"🍅 Завершено Pomodoro: {stats['pomodoros']}\n"
        f"☕ Коротких перерывов: {stats['short_breaks']}\n"
        f"🌴 Длинных перерывов: {stats['long_breaks']}\n\n"
        f"⚙️ Текущие настройки:\n"
        f"• Pomodoro: {pomodoro_min} мин\n"
        f"• Короткий перерыв: {short_min} мин\n"
        f"• Длинный перерыв: {long_min} мин\n"
    )
    
    if stats['pomodoros'] > 0:
        total_work_time = stats['pomodoros'] * intervals['pomodoro']
        stats_text += f"\n⏱ Всего времени работы: {total_work_time} секунд"
    
    await message.answer(stats_text, reply_markup=get_main_keyboard(user_id))


@dp.callback_query(F.data == "start_full_cycle")
async def start_full_cycle_handler(callback: CallbackQuery):
    """Запустить полный цикл Pomodoro"""
    user_id = callback.from_user.id
    
    # Проверяем, есть ли активный таймер или цикл
    if user_id in active_timers or user_id in active_cycles:
        await callback.answer("⏸ У вас уже запущен таймер или цикл! Остановите его перед запуском нового.", show_alert=True)
        return
    
    await callback.answer("🔄 Полный цикл Pomodoro запущен!")
    
    intervals = get_user_intervals(user_id)
    pomodoro_min = intervals['pomodoro'] // 60
    short_min = intervals['short_break'] // 60
    long_min = intervals['long_break'] // 60
    message = await callback.message.edit_text(
        f"🔄 Полный цикл Pomodoro запущен!\n\n"
        f"⚙️ Настройки:\n"
        f"• Pomodoro: {pomodoro_min} мин\n"
        f"• Короткий перерыв: {short_min} мин\n"
        f"• Длинный перерыв: {long_min} мин\n\n"
        f"Цикл будет работать до остановки.",
        reply_markup=None
    )
    
    # Запускаем цикл
    task = asyncio.create_task(run_full_cycle(
        callback.message.chat.id,
        message.message_id,
        user_id
    ))
    active_cycles[user_id] = task


@dp.callback_query(F.data == "set_pomodoro_interval")
async def set_pomodoro_interval(callback: CallbackQuery, state: FSMContext):
    """Начать настройку интервала Pomodoro"""
    user_id = callback.from_user.id
    
    if user_id in active_timers or user_id in active_cycles:
        await callback.answer("⏸ Остановите активный таймер или цикл перед изменением настроек!", show_alert=True)
        return
    
    intervals = get_user_intervals(user_id)
    pomodoro_min = intervals['pomodoro'] // 60
    await callback.answer()
    await callback.message.edit_text(
        f"🍅 Настройка интервала Pomodoro\n\n"
        f"Текущее значение: {pomodoro_min} минут\n\n"
        f"Введите новое значение в минутах (число):",
        reply_markup=get_settings_keyboard()
    )
    await state.set_state(PomodoroStates.waiting_for_pomodoro_interval)


@dp.callback_query(F.data == "set_short_break_interval")
async def set_short_break_interval(callback: CallbackQuery, state: FSMContext):
    """Начать настройку интервала короткого перерыва"""
    user_id = callback.from_user.id
    
    if user_id in active_timers or user_id in active_cycles:
        await callback.answer("⏸ Остановите активный таймер или цикл перед изменением настроек!", show_alert=True)
        return
    
    intervals = get_user_intervals(user_id)
    short_min = intervals['short_break'] // 60
    await callback.answer()
    await callback.message.edit_text(
        f"☕ Настройка интервала короткого перерыва\n\n"
        f"Текущее значение: {short_min} минут\n\n"
        f"Введите новое значение в минутах (число):",
        reply_markup=get_settings_keyboard()
    )
    await state.set_state(PomodoroStates.waiting_for_short_break_interval)


@dp.callback_query(F.data == "set_long_break_interval")
async def set_long_break_interval(callback: CallbackQuery, state: FSMContext):
    """Начать настройку интервала длинного перерыва"""
    user_id = callback.from_user.id
    
    if user_id in active_timers or user_id in active_cycles:
        await callback.answer("⏸ Остановите активный таймер или цикл перед изменением настроек!", show_alert=True)
        return
    
    intervals = get_user_intervals(user_id)
    long_min = intervals['long_break'] // 60
    await callback.answer()
    await callback.message.edit_text(
        f"🌴 Настройка интервала длинного перерыва\n\n"
        f"Текущее значение: {long_min} минут\n\n"
        f"Введите новое значение в минутах (число):",
        reply_markup=get_settings_keyboard()
    )
    await state.set_state(PomodoroStates.waiting_for_long_break_interval)


@dp.message(PomodoroStates.waiting_for_pomodoro_interval)
async def process_pomodoro_interval(message: Message, state: FSMContext):
    """Обработать ввод интервала Pomodoro"""
    try:
        value = int(message.text)
        if value <= 0:
            await message.answer("❌ Значение должно быть положительным числом! Попробуйте снова:")
            return
        
        intervals = get_user_intervals(message.from_user.id)
        intervals['pomodoro'] = value * 60
        await message.answer(
            f"✅ Интервал Pomodoro установлен: {value} минут",
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        await state.clear()
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число! Попробуйте снова:")


@dp.message(PomodoroStates.waiting_for_short_break_interval)
async def process_short_break_interval(message: Message, state: FSMContext):
    """Обработать ввод интервала короткого перерыва"""
    try:
        value = int(message.text)
        if value <= 0:
            await message.answer("❌ Значение должно быть положительным числом! Попробуйте снова:")
            return
        
        intervals = get_user_intervals(message.from_user.id)
        intervals['short_break'] = value * 60
        await message.answer(
            f"✅ Интервал короткого перерыва установлен: {value} минут",
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        await state.clear()
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число! Попробуйте снова:")


@dp.message(PomodoroStates.waiting_for_long_break_interval)
async def process_long_break_interval(message: Message, state: FSMContext):
    """Обработать ввод интервала длинного перерыва"""
    try:
        value = int(message.text)
        if value <= 0:
            await message.answer("❌ Значение должно быть положительным числом! Попробуйте снова:")
            return
        
        intervals = get_user_intervals(message.from_user.id)
        intervals['long_break'] = value * 60
        await message.answer(
            f"✅ Интервал длинного перерыва установлен: {value} минут",
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        await state.clear()
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число! Попробуйте снова:")


@dp.callback_query(F.data == "stop_timer")
async def stop_timer(callback: CallbackQuery):
    """Остановить активный таймер или цикл"""
    user_id = callback.from_user.id
    
    stopped = False
    
    # Останавливаем цикл если активен
    if user_id in active_cycles:
        active_cycles[user_id].cancel()
        del active_cycles[user_id]
        stopped = True
    
    # Останавливаем таймер если активен
    if user_id in active_timers:
        active_timers[user_id].cancel()
        del active_timers[user_id]
        stopped = True
    
    if not stopped:
        await callback.answer("❌ У вас нет активного таймера или цикла!", show_alert=True)
        return
    
    await callback.answer("⏹️ Остановлено!")
    await callback.message.edit_text(
        "⏹️ Таймер/цикл остановлен.\n\nВыберите действие:",
        reply_markup=get_main_keyboard(user_id)
    )


@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    """Вернуться в главное меню"""
    await state.clear()
    await callback.answer()
    user_id = callback.from_user.id
    intervals = get_user_intervals(user_id)
    pomodoro_min = intervals['pomodoro'] // 60
    short_min = intervals['short_break'] // 60
    long_min = intervals['long_break'] // 60
    text = (
        f"🍅 Главное меню\n\n"
        f"⚙️ Текущие настройки:\n"
        f"• Pomodoro: {pomodoro_min} мин\n"
        f"• Короткий перерыв: {short_min} мин\n"
        f"• Длинный перерыв: {long_min} мин"
    )
    await callback.message.edit_text(text, reply_markup=get_main_keyboard(user_id))


@dp.callback_query(F.data == "show_stats")
async def show_stats(callback: CallbackQuery):
    """Показать статистику"""
    user_id = callback.from_user.id
    stats = get_user_stats(user_id)
    intervals = get_user_intervals(user_id)
    pomodoro_min = intervals['pomodoro'] // 60
    short_min = intervals['short_break'] // 60
    long_min = intervals['long_break'] // 60
    stats_text = (
        f"📊 Ваша статистика:\n\n"
        f"🍅 Завершено Pomodoro: {stats['pomodoros']}\n"
        f"☕ Коротких перерывов: {stats['short_breaks']}\n"
        f"🌴 Длинных перерывов: {stats['long_breaks']}\n\n"
        f"⚙️ Текущие настройки:\n"
        f"• Pomodoro: {pomodoro_min} мин\n"
        f"• Короткий перерыв: {short_min} мин\n"
        f"• Длинный перерыв: {long_min} мин\n"
    )
    
    if stats['pomodoros'] > 0:
        total_work_time = stats['pomodoros'] * intervals['pomodoro']
        stats_text += f"\n⏱ Всего времени работы: {total_work_time} секунд"
    else:
        stats_text += "\n💡 Начните свой первый Pomodoro!"
    
    await callback.answer()
    await callback.message.edit_text(stats_text, reply_markup=get_main_keyboard(user_id))


async def main():
    """Главная функция для запуска бота"""
    print("🤖 Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
