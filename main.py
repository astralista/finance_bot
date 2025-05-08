import logging
import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, \
    filters, ContextTypes


# Функция форматирования денежных сумм
def format_money(amount):
    """
    Форматирует денежную сумму из 1234.56 в формат 1'234,56
    """
    # Округляем до 2 знаков после запятой
    rounded_amount = round(amount, 2)
    
    # Преобразуем в строку и проверяем, есть ли десятичная точка
    amount_str = str(rounded_amount)
    if '.' in amount_str:
        int_part, dec_part = amount_str.split('.')
    else:
        # Если точки нет, значит число целое
        int_part = amount_str
        dec_part = '0'
    
    # Форматируем целую часть с разделителями тысяч (апострофами)
    formatted_int = ''
    for i, digit in enumerate(reversed(int_part)):
        if i > 0 and i % 3 == 0:
            formatted_int = "'" + formatted_int
        formatted_int = digit + formatted_int
    
    # Убеждаемся, что дробная часть имеет 2 знака
    dec_part = dec_part.ljust(2, '0')
    
    # Соединяем части с запятой в качестве десятичного разделителя
    return f"{formatted_int},{dec_part}"

# Загружаем переменные окружения из файла .env
load_dotenv()

# Включаем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
(
    CATEGORY_NAME, CATEGORY_EDIT, CATEGORY_DELETE,
    SET_LIMIT, EDIT_LIMIT,
    ADD_EXPENSE, EXPENSE_AMOUNT
) = range(7)


# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()

    # Таблица категорий
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Таблица лимитов по категориям
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS limits (
        id INTEGER PRIMARY KEY,
        category_id INTEGER,
        amount REAL,
        month INTEGER,
        year INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (category_id) REFERENCES categories (id),
        UNIQUE(category_id, month, year)
    )
    ''')

    # Таблица расходов
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY,
        category_id INTEGER,
        amount REAL,
        date DATE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (category_id) REFERENCES categories (id)
    )
    ''')

    conn.commit()
    conn.close()


# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'Привет! Я бот для отслеживания расходов. '
        'Вы можете управлять категориями расходов и отслеживать лимиты.\n\n'
        'Доступные команды:\n'
        '/categories - управление категориями\n'
        '/limits - управление лимитами расходов\n'
        '/expense - добавить расход\n'
        '/report - показать отчет по расходам'
    )


# Команда для управления категориями
async def categories_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Список категорий", callback_data='list_categories')],
        [InlineKeyboardButton("Добавить категорию", callback_data='add_category')],
        [InlineKeyboardButton("Редактировать категорию", callback_data='edit_category')],
        [InlineKeyboardButton("Удалить категорию", callback_data='delete_category')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Выберите действие:', reply_markup=reply_markup)


# Получить список категорий из БД
def get_categories():
    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM categories ORDER BY name")
    categories = cursor.fetchall()
    conn.close()
    return categories


# Обработка списка категорий
async def list_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    categories = get_categories()

    if not categories:
        await query.edit_message_text("У вас еще нет категорий. Создайте их с помощью команды 'Добавить категорию'.")
        return

    current_month = datetime.now().month
    current_year = datetime.now().year

    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()

    result = "📋 Список категорий и остаток лимита:\n\n"

    for cat_id, cat_name in categories:
        # Получаем лимит для текущего месяца
        cursor.execute("""
            SELECT amount FROM limits 
            WHERE category_id = ? AND month = ? AND year = ?
        """, (cat_id, current_month, current_year))
        limit_data = cursor.fetchone()
        limit_amount = limit_data[0] if limit_data else 0

        # Получаем сумму расходов по категории за текущий месяц
        cursor.execute("""
            SELECT SUM(amount) FROM expenses 
            WHERE category_id = ? AND strftime('%m', date) = ? AND strftime('%Y', date) = ?
        """, (cat_id, f"{current_month:02d}", str(current_year)))

        spent_data = cursor.fetchone()
        spent_amount = spent_data[0] if spent_data[0] else 0

        # Вычисляем остаток
        remaining = limit_amount - spent_amount

        if remaining >= 0:
            result += f"✅ {cat_name}: осталось {format_money(remaining)} из {format_money(limit_amount)}\n"
        else:
            result += f"❌ {cat_name}: перерасход {format_money(abs(remaining))} (лимит {format_money(limit_amount)})\n"

    conn.close()
    await query.edit_message_text(result)


# Начало добавления категории
async def add_category_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text("Введите название новой категории:")
    return CATEGORY_NAME


# Завершение добавления категории
async def add_category_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category_name = update.message.text.strip()

    if not category_name:
        await update.message.reply_text("Название категории не может быть пустым. Попробуйте снова.")
        return CATEGORY_NAME

    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()

    try:
        cursor.execute("INSERT INTO categories (name) VALUES (?)", (category_name,))
        conn.commit()
        await update.message.reply_text(f"Категория '{category_name}' успешно добавлена!")
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"Категория с названием '{category_name}' уже существует.")
    finally:
        conn.close()

    return ConversationHandler.END


# Начало редактирования категории
async def edit_category_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    categories = get_categories()

    if not categories:
        await query.edit_message_text("У вас еще нет категорий для редактирования.")
        return ConversationHandler.END

    keyboard = []
    for cat_id, cat_name in categories:
        keyboard.append([InlineKeyboardButton(cat_name, callback_data=f'edit_{cat_id}')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите категорию для редактирования:", reply_markup=reply_markup)
    return CATEGORY_EDIT


# Запрос нового имени категории
async def edit_category_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat_id = query.data.split('_')[1]
    context.user_data['edit_category_id'] = cat_id

    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM categories WHERE id = ?", (cat_id,))
    cat_name = cursor.fetchone()[0]
    conn.close()

    await query.edit_message_text(f"Текущее название: {cat_name}\nВведите новое название категории:")
    return CATEGORY_EDIT


# Завершение редактирования категории
async def edit_category_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    cat_id = context.user_data.get('edit_category_id')

    if not new_name:
        await update.message.reply_text("Название категории не может быть пустым. Попробуйте снова.")
        return CATEGORY_EDIT

    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()

    try:
        cursor.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, cat_id))
        conn.commit()
        await update.message.reply_text(f"Название категории успешно изменено на '{new_name}'!")
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"Категория с названием '{new_name}' уже существует.")
    finally:
        conn.close()

    return ConversationHandler.END


# Начало удаления категории
async def delete_category_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    categories = get_categories()

    if not categories:
        await query.edit_message_text("У вас еще нет категорий для удаления.")
        return ConversationHandler.END

    keyboard = []
    for cat_id, cat_name in categories:
        keyboard.append([InlineKeyboardButton(cat_name, callback_data=f'delete_{cat_id}')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите категорию для удаления:", reply_markup=reply_markup)
    return CATEGORY_DELETE


# Подтверждение удаления категории
async def delete_category_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat_id = query.data.split('_')[1]

    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM categories WHERE id = ?", (cat_id,))
    cat_name = cursor.fetchone()[0]
    conn.close()

    context.user_data['delete_category_id'] = cat_id
    context.user_data['delete_category_name'] = cat_name

    keyboard = [
        [InlineKeyboardButton("Да, удалить", callback_data=f'confirm_delete_{cat_id}')],
        [InlineKeyboardButton("Нет, отмена", callback_data='cancel_delete')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"Вы уверены, что хотите удалить категорию '{cat_name}'?\n"
        "Все связанные расходы и лимиты также будут удалены.",
        reply_markup=reply_markup
    )
    return CATEGORY_DELETE


# Завершение удаления категории
async def delete_category_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith('confirm_delete_'):
        cat_id = context.user_data.get('delete_category_id')
        cat_name = context.user_data.get('delete_category_name')

        conn = sqlite3.connect('expenses.db')
        cursor = conn.cursor()

        # Удаляем все связанные записи
        cursor.execute("DELETE FROM expenses WHERE category_id = ?", (cat_id,))
        cursor.execute("DELETE FROM limits WHERE category_id = ?", (cat_id,))
        cursor.execute("DELETE FROM categories WHERE id = ?", (cat_id,))

        conn.commit()
        conn.close()

        await query.edit_message_text(f"Категория '{cat_name}' и все связанные данные удалены.")
    else:
        await query.edit_message_text("Удаление категории отменено.")

    return ConversationHandler.END


# Команда для управления лимитами
async def limits_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Установить лимит", callback_data='set_limit')],
        [InlineKeyboardButton("Изменить лимит", callback_data='edit_limit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Управление лимитами расходов:', reply_markup=reply_markup)


# Начало установки лимита
async def set_limit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    categories = get_categories()

    if not categories:
        await query.edit_message_text("У вас еще нет категорий. Создайте их сначала.")
        return ConversationHandler.END

    keyboard = []
    for cat_id, cat_name in categories:
        keyboard.append([InlineKeyboardButton(cat_name, callback_data=f'setlimit_{cat_id}')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите категорию для установки лимита:", reply_markup=reply_markup)
    return SET_LIMIT


# Выбор категории для установки лимита
async def set_limit_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat_id = query.data.split('_')[1]
    context.user_data['limit_category_id'] = cat_id

    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM categories WHERE id = ?", (cat_id,))
    cat_name = cursor.fetchone()[0]
    context.user_data['limit_category_name'] = cat_name

    current_month = datetime.now().month
    current_year = datetime.now().year

    cursor.execute("""
        SELECT amount FROM limits 
        WHERE category_id = ? AND month = ? AND year = ?
    """, (cat_id, current_month, current_year))

    limit_data = cursor.fetchone()
    current_limit = limit_data[0] if limit_data else 0
    conn.close()

    await query.edit_message_text(
        f"Категория: {cat_name}\n"
        f"Текущий лимит на {current_month}/{current_year}: {format_money(current_limit)}\n\n"
        "Введите новый лимит расходов для этой категории:"
    )
    return SET_LIMIT


# Завершение установки лимита
async def set_limit_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit_amount = float(update.message.text.strip())
        if limit_amount < 0:
            await update.message.reply_text(
                "Лимит не может быть отрицательным. Пожалуйста, введите положительное число.")
            return SET_LIMIT
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите корректное число.")
        return SET_LIMIT

    cat_id = context.user_data.get('limit_category_id')
    cat_name = context.user_data.get('limit_category_name')
    current_month = datetime.now().month
    current_year = datetime.now().year

    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()

    # Пробуем обновить существующий лимит или создать новый
    cursor.execute("""
        INSERT OR REPLACE INTO limits (category_id, amount, month, year)
        VALUES (?, ?, ?, ?)
    """, (cat_id, limit_amount, current_month, current_year))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"Лимит для категории '{cat_name}' на {current_month}/{current_year} "
        f"установлен: {format_money(limit_amount)}"
    )

    return ConversationHandler.END


# Начало добавления расхода
async def add_expense_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = get_categories()

    if not categories:
        await update.message.reply_text("У вас еще нет категорий. Создайте их сначала с помощью /categories.")
        return ConversationHandler.END

    keyboard = []
    for cat_id, cat_name in categories:
        keyboard.append([InlineKeyboardButton(cat_name, callback_data=f'expense_{cat_id}')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите категорию расхода:", reply_markup=reply_markup)
    return ADD_EXPENSE


# Выбор категории для добавления расхода
async def add_expense_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat_id = query.data.split('_')[1]
    context.user_data['expense_category_id'] = cat_id

    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM categories WHERE id = ?", (cat_id,))
    cat_name = cursor.fetchone()[0]
    context.user_data['expense_category_name'] = cat_name
    conn.close()

    await query.edit_message_text(f"Категория: {cat_name}\nВведите сумму расхода:")
    return EXPENSE_AMOUNT


# Завершение добавления расхода
async def add_expense_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        expense_amount = float(update.message.text.strip())
        if expense_amount <= 0:
            await update.message.reply_text(
                "Сумма расхода должна быть положительным числом. Пожалуйста, введите корректное значение.")
            return EXPENSE_AMOUNT
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите корректное число.")
        return EXPENSE_AMOUNT

    cat_id = context.user_data.get('expense_category_id')
    cat_name = context.user_data.get('expense_category_name')
    today = datetime.now().date().isoformat()

    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()

    # Добавляем расход
    cursor.execute("""
        INSERT INTO expenses (category_id, amount, date)
        VALUES (?, ?, ?)
    """, (cat_id, expense_amount, today))

    # Получаем текущий лимит и расходы
    current_month = datetime.now().month
    current_year = datetime.now().year

    cursor.execute("""
        SELECT amount FROM limits 
        WHERE category_id = ? AND month = ? AND year = ?
    """, (cat_id, current_month, current_year))

    limit_data = cursor.fetchone()
    limit_amount = limit_data[0] if limit_data else 0

    # Получаем сумму расходов по категории за текущий месяц
    cursor.execute("""
        SELECT SUM(amount) FROM expenses 
        WHERE category_id = ? AND strftime('%m', date) = ? AND strftime('%Y', date) = ?
    """, (cat_id, f"{current_month:02d}", str(current_year)))

    spent_data = cursor.fetchone()
    spent_amount = spent_data[0] if spent_data[0] else 0

    conn.commit()
    conn.close()

    # Вычисляем остаток
    remaining = limit_amount - spent_amount

    if remaining >= 0:
        message = (
            f"✅ Расход записан. Категория '{cat_name}'\n"
            f"Потрачено: {format_money(expense_amount)}\n"
            f"Осталось до лимита: {format_money(remaining)}"
        )
    else:
        message = (
            f"❌ Расход записан. Категория '{cat_name}'\n"
            f"Потрачено: {format_money(expense_amount)}\n"
            f"Внимание! Перерасход: {format_money(abs(remaining))}"
        )

    await update.message.reply_text(message)
    return ConversationHandler.END


# Отчет по расходам
async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_month = datetime.now().month
    current_year = datetime.now().year

    conn = sqlite3.connect('expenses.db')
    cursor = conn.cursor()

    # Получаем все категории
    cursor.execute("SELECT id, name FROM categories ORDER BY name")
    categories = cursor.fetchall()

    if not categories:
        await update.message.reply_text("У вас еще нет категорий для отчета.")
        conn.close()
        return

    report = f"📊 Отчет за {current_month}/{current_year}:\n\n"
    total_limit = 0
    total_spent = 0

    for cat_id, cat_name in categories:
        # Получаем лимит
        cursor.execute("""
            SELECT amount FROM limits 
            WHERE category_id = ? AND month = ? AND year = ?
        """, (cat_id, current_month, current_year))

        limit_data = cursor.fetchone()
        limit_amount = limit_data[0] if limit_data else 0
        total_limit += limit_amount

        # Получаем расходы
        cursor.execute("""
            SELECT SUM(amount) FROM expenses 
            WHERE category_id = ? AND strftime('%m', date) = ? AND strftime('%Y', date) = ?
        """, (cat_id, f"{current_month:02d}", str(current_year)))

        spent_data = cursor.fetchone()
        spent_amount = spent_data[0] if spent_data[0] else 0
        total_spent += spent_amount

        # Вычисляем процент использования лимита
        if limit_amount > 0:
            usage_percent = (spent_amount / limit_amount) * 100
            status = "✅" if spent_amount <= limit_amount else "❌"
        else:
            usage_percent = 0
            status = "⚠️"

        report += f"{status} {cat_name}:\n"
        report += f"   Лимит: {format_money(limit_amount)}\n"
        report += f"   Потрачено: {format_money(spent_amount)} ({usage_percent:.1f}%)\n\n"

    # Общая статистика
    if total_limit > 0:
        total_percent = (total_spent / total_limit) * 100
        total_status = "✅" if total_spent <= total_limit else "❌"
    else:
        total_percent = 0
        total_status = "⚠️"

    report += f"ИТОГО {total_status}:\n"
    report += f"Общий лимит: {format_money(total_limit)}\n"
    report += f"Общие расходы: {format_money(total_spent)} ({total_percent:.1f}%)"

    conn.close()
    await update.message.reply_text(report)


# Функция отмены диалога
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END


# Главная функция
def main():
    # Инициализация базы данных
    init_db()

    # Получаем токен из переменных окружения
    bot_token = os.getenv("TGbotTOKEN")
    if not bot_token:
        raise ValueError("Не найден токен бота! Убедитесь, что TGbotTOKEN указан в файле .env")

    # Создаем экземпляр приложения
    application = Application.builder().token(bot_token).build()

    # Добавляем обработчики основных команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("report", show_report))

    # Обработчик для категорий
    application.add_handler(CommandHandler("categories", categories_menu))

    # ConversationHandler для добавления категории
    add_category_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_category_start, pattern='^add_category$')],
        states={
            CATEGORY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_category_finish)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(add_category_conv)

    # ConversationHandler для редактирования категории
    edit_category_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_category_start, pattern='^edit_category$')],
        states={
            CATEGORY_EDIT: [
                CallbackQueryHandler(edit_category_select, pattern='^edit_\d+$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_category_finish)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(edit_category_conv)

    # ConversationHandler для удаления категории
    delete_category_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(delete_category_start, pattern='^delete_category$')],
        states={
            CATEGORY_DELETE: [
                CallbackQueryHandler(delete_category_confirm, pattern='^delete_\d+$'),
                CallbackQueryHandler(delete_category_finish, pattern='^confirm_delete_\d+$|^cancel_delete$')
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(delete_category_conv)

    # Обработчик для лимитов
    application.add_handler(CommandHandler("limits", limits_menu))

    # ConversationHandler для установки лимита
    set_limit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_limit_start, pattern='^set_limit$')],
        states={
            SET_LIMIT: [
                CallbackQueryHandler(set_limit_category, pattern='^setlimit_\d+$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_limit_finish)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(set_limit_conv)

    # ConversationHandler для добавления расхода
    add_expense_conv = ConversationHandler(
        entry_points=[CommandHandler("expense", add_expense_start)],
        states={
            ADD_EXPENSE: [CallbackQueryHandler(add_expense_category, pattern='^expense_\d+$')],
            EXPENSE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_expense_finish)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(add_expense_conv)

    # Обработчик для отображения списка категорий
    application.add_handler(CallbackQueryHandler(list_categories, pattern='^list_categories$'))

    # Запуск бота
    application.run_polling()


if __name__ == "__main__":
    main()
