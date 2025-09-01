from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Main menu keyboard for drivers

def login_menu() -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text="Kirish")]]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def main_menu(is_running: bool) -> ReplyKeyboardMarkup:
    """
    Main menu keyboard for drivers.
    
    Args:
        is_running: Whether the driver has an active announcement running
    """
    if is_running:
        keyboard = [
            [
                KeyboardButton(text="Stop"),
                KeyboardButton(text="Yangi xabar"),
            ],
        ]
    else:
        keyboard = [
            [
                KeyboardButton(text="Xabar Yaratish"),
                KeyboardButton(text="Guruhlar papkasi")
            ],
        ]
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# Admin menu keyboard
admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Haydovchi qo'shish"),
            KeyboardButton(text="🚗 Haydovchilar"),
        ],
        [
            KeyboardButton(text="Qidirish"),
        ],
    ],
    resize_keyboard=True,
)

# Driver details keyboard for admin
def driver_details_keyboard(driver_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """
    Keyboard for driver details when viewed by admin.
    
    Args:
        driver_id: Telegram ID of the driver
        is_active: Whether the driver is currently active
    """
    buttons = []
    
    # Add extend button
    buttons.append([InlineKeyboardButton(
        text="+30 kunga uzaytirish",
        callback_data=f"extend:{driver_id}"
    )])
    
    # Add activate/deactivate button
    if is_active:
        buttons.append([InlineKeyboardButton(
            text="O'chirish",
            callback_data=f"deactivate:{driver_id}"
        )])
        buttons.append([InlineKeyboardButton(
            text="ID ozgartirish",
            callback_data=f"change:{driver_id}"
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text="Faollashtirish",
            callback_data=f"activate:{driver_id}"
        )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Pagination keyboard for drivers list
def pagination_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """
    Pagination keyboard for drivers list.
    
    Args:
        page: Current page number
        total_pages: Total number of pages
    """
    buttons = []
    
    # Add navigation buttons
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️ ",
            callback_data=f"page_:{page-1}"
        ))
    
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(
            text=" ▶️",
            callback_data=f"page_:{page+1}"
        ))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)
