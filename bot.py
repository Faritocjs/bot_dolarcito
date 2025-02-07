import requests
import logging
import asyncio
import nest_asyncio
import re
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    CallbackContext,
    MessageHandler,
    filters
)
from datetime import datetime
import aiohttp

# Aplica nest_asyncio para entornos con event loop en ejecución
nest_asyncio.apply()

# Reemplaza con tu token real
TOKEN = "7965785438:AAHTxP_noJ-Ojc2FRTXQZE-AtR29JqAWhPU"

# Configuración global
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

last_rates = {}      # Para caché (opcional)
subscribers = set()  # Usuarios suscriptos a alertas
THRESHOLD_PERCENT = 1.0

# Configuración de las fuentes de cotización
CURRENCY_SOURCES = {
    "USD": [
        {
            "url": "https://dolarhoy.com/",
            "type": "html",
            "selectors": [
                'div.value',
                'div.val',
                'div.compra div.val',
                'div.venta div.val'
            ]
        },
        {
            "url": "https://www.cronista.com/MercadosOnline/moneda.html?id=ARSB",
            "type": "html",
            "selectors": [
                'div.buy-value',
                'div.sell-value'
            ]
        },
        {
            "url": "https://www.ambito.com/contenidos/dolar-informal.html",
            "type": "html",
            "selectors": [
                'div.data-valor',
                'div.value'
            ]
        }
    ],
    "EUR": [
        {
            "url": "https://www.dolarito.ar/cotizacion/euro-hoy",
            "type": "html",
            "selectors": [
                "div.value",
                "span.value"
            ]
        },
        {
            "url": "https://www.precioeuroblue.com.ar/",
            "type": "html",
            # Se usará extracción personalizada a partir del contenido completo
            "selectors": ["body"]
        }
    ],
    "CLP": [
        {
            "url": "https://wise.com/es/currency-converter/clp-to-ars-rate",
            "type": "html",
            "selectors": [
                'span.text-success',
                'div.rate-value'
            ]
        },
        {
            "url": "https://www.cronista.com/MercadosOnline/moneda.html?id=ARSC",
            "type": "html",
            "selectors": [
                'div.buy-value',
                'div.sell-value'
            ]
        }
    ]
}

# Función para obtener el contenido de una URL
async def fetch_rate(session, url):
    try:
        async with session.get(url, headers=HEADERS, timeout=15) as response:
            if response.status == 200:
                return await response.text()
            logging.error(f"Error status {response.status} para {url}")
            return None
    except Exception as e:
        logging.error(f"Error fetching {url}: {e}")
        return None

# Función para extraer el primer número flotante de un texto
def extract_rate_from_text(text):
    text = re.sub(r'[$ €]', '', text.strip())
    text = text.replace(',', '.')
    match = re.search(r'(\d+(?:\.\d+)?)', text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None

# Función personalizada para extraer la cotización del Euro Blue usando re.findall
def extract_euroblue(content):
    numbers = re.findall(r"([\d]{4,}\.\d{2})", content)
    if len(numbers) >= 2:
        compra_val = float(numbers[0])
        venta_val = float(numbers[1])
        return compra_val, venta_val
    return None

# Función genérica para extraer números usando los selectores dados
async def get_rates_from_html(content, selectors):
    soup = BeautifulSoup(content, 'html.parser')
    rates = []
    for selector in selectors:
        elements = soup.select(selector)
        for element in elements:
            rate = extract_rate_from_text(element.text)
            if rate is not None:
                rates.append(rate)
    return rates

# Obtiene las tasas de compra y venta para la moneda
async def get_currency_rate(currency):
    async with aiohttp.ClientSession() as session:
        all_rates = []
        for source in CURRENCY_SOURCES[currency]:
            try:
                content = await fetch_rate(session, source['url'])
                if not content:
                    continue
                # Para la fuente de precioeuroblue.com.ar, usamos la extracción personalizada
                if currency == "EUR" and "precioeuroblue.com.ar" in source['url']:
                    custom = extract_euroblue(content)
                    if custom is not None:
                        compra_val, venta_val = custom
                        all_rates.append({'compra': compra_val, 'venta': venta_val})
                        continue
                # Caso general: uso de selectores
                if source['type'] == 'html':
                    rates = await get_rates_from_html(content, source['selectors'])
                    if len(rates) >= 2:
                        all_rates.append({
                            'compra': min(rates[:2]),
                            'venta': max(rates[:2])
                        })
                    elif len(rates) == 1:
                        all_rates.append({
                            'compra': rates[0],
                            'venta': rates[0]
                        })
            except Exception as e:
                logging.error(f"Error procesando {source['url']}: {e}")
                continue
        if all_rates:
            compras = [r['compra'] for r in all_rates]
            ventas = [r['venta'] for r in all_rates]
            return {
                'compra': sorted(compras)[len(compras)//2],
                'venta': sorted(ventas)[len(ventas)//2]
            }
        return None

# Formatea el mensaje con la cotización
async def format_currency_message(currency, rates):
    if not rates:
        return f"❌ No se pudieron obtener las cotizaciones para {currency}"
    emoji_map = {
        "USD": "💵",
        "EUR": "💶",
        "CLP": "🇨🇱"
    }
    emoji = emoji_map.get(currency, "💱")
    if currency == "CLP":
        return f"""
{emoji} *Cotización {currency}*
━━━━━━━━━━━━━━━
💰 *Compra:* ${rates['compra']*1000:.2f} por 1000 {currency}
💳 *Venta:* ${rates['venta']*1000:.2f} por 1000 {currency}
🕒 Actualizado: {datetime.now().strftime('%H:%M:%S')}
"""
    else:
        return f"""
{emoji} *Cotización {currency}*
━━━━━━━━━━━━━━━
💰 *Compra:* ${rates['compra']:.2f}
💳 *Venta:* ${rates['venta']:.2f}
🕒 Actualizado: {datetime.now().strftime('%H:%M:%S')}
"""

# Comando /start y botón de inicio (se eliminó la sección de "Comandos disponibles")
async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    welcome_message = f"""
🌟 ¡Hola {user.first_name}! 

Bienvenido a tu asistente de cotizaciones 🤖
━━━━━━━━━━━━━━━━━━━━━━━
Puedo ayudarte con:
📊 Cotizaciones en tiempo real
💱 Conversiones entre monedas
⏰ Alertas de cambios importantes
"""
    keyboard = [
        [
            InlineKeyboardButton("💵 USD", callback_data="rate_USD"),
            InlineKeyboardButton("💶 EUR", callback_data="rate_EUR"),
            InlineKeyboardButton("🇨🇱 CLP", callback_data="rate_CLP")
        ],
        [InlineKeyboardButton("💱 Convertir monedas", callback_data="convert")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode="Markdown")

# Callback para mostrar cotizaciones
async def rate_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    currency = query.data.split("_")[1]
    rates = await get_currency_rate(currency)
    message = await format_currency_message(currency, rates)
    keyboard = [
        [InlineKeyboardButton("🔄 Actualizar", callback_data=f"rate_{currency}")],
        [InlineKeyboardButton("🔙 Volver", callback_data="start")]
    ]
    await query.edit_message_text(
        text=message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# Flujo de conversión de monedas

# Inicia la conversión y permite seleccionar la moneda de origen (incluyendo ARS)
async def convert_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data['convert_state'] = 'select_from'
    keyboard = [
        [
            InlineKeyboardButton("ARS", callback_data="from_ARS"),
            InlineKeyboardButton("USD", callback_data="from_USD"),
            InlineKeyboardButton("EUR", callback_data="from_EUR"),
            InlineKeyboardButton("CLP", callback_data="from_CLP")
        ],
        [InlineKeyboardButton("🔙 Volver", callback_data="start")]
    ]
    await query.edit_message_text(
        "Selecciona la moneda de origen:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Selección de moneda origen
async def from_currency_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    from_currency = query.data.split("_")[1]
    context.user_data['from_currency'] = from_currency
    context.user_data['convert_state'] = 'select_to'
    keyboard = [
        [
            InlineKeyboardButton("ARS", callback_data="to_ARS"),
            InlineKeyboardButton("USD", callback_data="to_USD"),
            InlineKeyboardButton("EUR", callback_data="to_EUR"),
            InlineKeyboardButton("CLP", callback_data="to_CLP")
        ],
        [InlineKeyboardButton("🔙 Volver", callback_data="convert")]
    ]
    await query.edit_message_text(
        f"Seleccionaste {from_currency}. Ahora elige la moneda destino:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Selección de moneda destino
async def to_currency_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    to_currency = query.data.split("_")[1]
    context.user_data['to_currency'] = to_currency
    context.user_data['convert_state'] = 'enter_amount'
    await query.edit_message_text(
        f"Conversión de {context.user_data.get('from_currency', '')} a {to_currency}\n"
        "Por favor, envía el monto a convertir:"
    )

# Maneja el monto ingresado y realiza la conversión
async def handle_conversion_amount(update: Update, context: CallbackContext):
    if context.user_data.get('convert_state') != 'enter_amount':
        return
    try:
        amount = float(update.message.text.replace(',', '.'))
        from_curr = context.user_data.get('from_currency')
        to_curr = context.user_data.get('to_currency')
        from_rates = await get_currency_rate(from_curr) if from_curr != "ARS" else {"compra": 1, "venta": 1}
        to_rates = await get_currency_rate(to_curr) if to_curr != "ARS" else {"compra": 1, "venta": 1}
        if not from_rates or not to_rates:
            await update.message.reply_text("❌ Error obteniendo las tasas de cambio.")
            return
        if from_curr == "ARS":
            result = amount / to_rates['venta']
        elif to_curr == "ARS":
            result = amount * from_rates['compra']
        else:
            result = (amount * from_rates['compra']) / to_rates['venta']
        message = f"""
💱 *Resultado de la conversión*
━━━━━━━━━━━━━━━━━━━
{amount:.2f} {from_curr} = {result:.2f} {to_curr}

_Tasas utilizadas:_
{from_curr}: Compra ${from_rates['compra']:.2f} / Venta ${from_rates['venta']:.2f}
{to_curr}: Compra ${to_rates['compra']:.2f} / Venta ${to_rates['venta']:.2f}
"""
        context.user_data.clear()
        keyboard = [[InlineKeyboardButton("🔄 Nueva conversión", callback_data="convert")]]
        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Por favor ingresa un número válido.")
    except Exception as e:
        logging.error(f"Error en la conversión: {e}")
        await update.message.reply_text("❌ Ocurrió un error en la conversión.")

# Manejo global de errores (se evita error si update es None)
async def error_handler(update: object, context: CallbackContext):
    logging.error(f"Error: {context.error}")
    if update is not None and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ Ocurrió un error. Por favor intenta nuevamente.")
        except Exception:
            pass

# Función principal
async def main():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(rate_callback, pattern="^rate_"))
    app.add_handler(CallbackQueryHandler(convert_callback, pattern="^convert$"))
    app.add_handler(CallbackQueryHandler(from_currency_callback, pattern="^from_"))
    app.add_handler(CallbackQueryHandler(to_currency_callback, pattern="^to_"))
    app.add_handler(CallbackQueryHandler(start, pattern="^start$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_conversion_amount))
    app.add_error_handler(error_handler)
    
    await app.bot.delete_webhook()
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
