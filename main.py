import asyncio
from api.mexc import Mexc
import os
import requests
import random
from config import (
    DELAY_BETWEEN_CHECK_POSITIONS,
    VOL_RANDOM_MIN,
    VOL_RANDOM_MAX,
    LEVERAGE_RANDOM_MIN,
    LEVERAGE_RANDOM_MAX,
    TELEGRAM_CHAT_ID,
    TELEGRAM_BOT_TOKEN
)

# === TELEGRAM ===


async def send_telegram_message(message):
    """Отправка сообщения в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"❌ Ошибка отправки в Telegram: {response.text}")
    except Exception as e:
        print(f"❌ Ошибка отправки в Telegram: {e}")

def load_accounts():
    accounts = []
    accounts_dir = "./accounts"
    for filename in os.listdir(accounts_dir):
        filepath = os.path.join(accounts_dir, filename)
        if os.path.isfile(filepath):
            if filename == "main.txt":
                continue
            with open(filepath, "r") as file:
                for line in file:
                    parts = line.strip().split("|")
                    if parts and parts[0]:
                        uid = parts[0]
                        proxy = parts[1] if len(parts) > 1 else None
                        accounts.append((uid, proxy))
    return accounts


async def open_positions(main_mexc, mexcs: list[Mexc], symbol, side, leverage, stop_loss_price, vol, open_type):
    tasks = []
    print(
        f"🚀 Открываем позицию {symbol} ({side}) для всех аккаунтов | SL={stop_loss_price} | базовый vol={vol} | базовый leverage={leverage} | openType={open_type}")

    # Получаем текущую цену и маржу для главного аккаунта из истории ордеров
    main_margin = "N/A"
    main_entry_price = "N/A"

    try:
        main_orders = await main_mexc.get_order_history(symbol, limit=5)
        # Ищем последний открывающий ордер
        open_orders = [order for order in main_orders if order.get('side') in [1, 3] and order.get('state') == 3]
        if open_orders:
            latest_open = open_orders[0]
            main_entry_price = latest_open.get('dealAvgPrice', latest_open.get('dealAvgPriceStr', 'N/A'))
            main_margin = latest_open.get('orderMargin', 'N/A')
    except Exception as e:
        print(f"⚠️ Ошибка получения данных главного аккаунта: {e}")

    account_results = []

    for mexc in mexcs:
        # Генерируем случайный leverage для каждого аккаунта
        random_leverage = random.randint(LEVERAGE_RANDOM_MIN, LEVERAGE_RANDOM_MAX)

        # Генерируем случайное изменение vol в процентах
        vol_change_percent = random.randint(VOL_RANDOM_MIN, VOL_RANDOM_MAX)
        random_vol = int(vol * (1 + vol_change_percent / 100))

        # Убеждаемся что vol >= 1
        random_vol = max(1, random_vol)

        print(f"  📊 Аккаунт: leverage={random_leverage}, vol={random_vol} ({vol_change_percent:+d}%)")

        tasks.append(mexc.open_position(
            symbol, side, random_leverage, stop_loss_price, random_vol, open_type))
        account_results.append((random_leverage, random_vol))

    results = await asyncio.gather(*tasks)

    # Получаем маржу и цены входа для ведомых аккаунтов из истории ордеров
    slave_data = []
    for mexc, (acc_leverage, acc_vol) in zip(mexcs, account_results):
        try:
            orders = await mexc.get_order_history(symbol, limit=5)
            # Ищем последний открывающий ордер
            open_orders = [order for order in orders if order.get('side') in [1, 3] and order.get('state') == 3]

            entry_price = "N/A"
            margin = "N/A"

            if open_orders:
                latest_open = open_orders[0]
                entry_price = latest_open.get('dealAvgPrice', latest_open.get('dealAvgPriceStr', 'N/A'))
                margin = latest_open.get('orderMargin', 'N/A')

            slave_data.append({
                'leverage': acc_leverage,
                'vol': acc_vol,
                'entry_price': entry_price,
                'margin': margin
            })
        except Exception as e:
            print(f"⚠️ Ошибка получения данных аккаунта: {e}")
            slave_data.append({
                'leverage': acc_leverage,
                'vol': acc_vol,
                'entry_price': 'N/A',
                'margin': 'N/A'
            })

    # Формируем сообщение для Telegram с реальной маржой
    side_text = "LONG" if side == 1 else "SHORT"
    message = f"🚀 Открытие позиции {symbol} side={side_text} x{leverage}\n\n"
    message += f"<b>Главный аккаунт:</b>\n"
    message += f"  Маржа: ${main_margin}\n" if main_margin != "N/A" else f"  Маржа: расчет недоступен\n"
    message += f"  Вход: {main_entry_price}\n\n"
    message += f"<b>Ведомые аккаунты:</b>\n"

    for i, acc_data in enumerate(slave_data, 1):
        margin = acc_data['margin']
        entry_price = acc_data['entry_price']

        if margin != "N/A" and margin:
            message += f"{i}) маржа=${float(margin):.2f}, вход={entry_price}\n"
        else:
            message += f"{i}) маржа=расчет недоступен, вход={entry_price}\n"

    await send_telegram_message(message)
    return results


async def open_limit_orders(mexcs: list[Mexc], symbol, side, leverage, price, vol, open_type):
    """Открыть лимитные ордера на всех аккаунтах"""
    tasks = []
    print(
        f"📝 Открываем лимитный ордер {symbol} ({side}) для всех аккаунтов | price={price} | базовый vol={vol} | базовый leverage={leverage} | openType={open_type}")

    for mexc in mexcs:
        # Генерируем случайный leverage для каждого аккаунта
        random_leverage = random.randint(
            LEVERAGE_RANDOM_MIN, LEVERAGE_RANDOM_MAX)

        # Генерируем случайное изменение vol в процентах
        vol_change_percent = random.randint(VOL_RANDOM_MIN, VOL_RANDOM_MAX)
        random_vol = int(vol * (1 + vol_change_percent / 100))

        # Убеждаемся что vol >= 1
        random_vol = max(1, random_vol)

        print(
            f"  📊 Аккаунт: leverage={random_leverage}, vol={random_vol} ({vol_change_percent:+d}%)")

        tasks.append(mexc.open_position_limit(
            symbol, side, random_leverage, price, random_vol, open_type))

    results = await asyncio.gather(*tasks)
    return results


async def change_limit_orders(mexcs_orders: list, price: str, vol: int):
    """Изменить лимитные ордера на всех аккаунтах. Возвращает новые order_id"""
    tasks = []
    print(f"✏️ Изменяем лимитные ордера | price={price} | базовый vol={vol}")

    for mexc, order_id in mexcs_orders:
        # Генерируем случайное изменение vol в процентах
        vol_change_percent = random.randint(VOL_RANDOM_MIN, VOL_RANDOM_MAX)
        random_vol = int(vol * (1 + vol_change_percent / 100))

        # Убеждаемся что vol >= 1
        random_vol = max(1, random_vol)

        print(
            f"  📊 Изменяем ордер {order_id}: vol={random_vol} ({vol_change_percent:+d}%)")

        tasks.append(mexc.change_limit_order(order_id, price, random_vol))

    results = await asyncio.gather(*tasks)

    # Парсим новые orderId из ответов
    new_orders = []
    for (mexc, old_order_id), result in zip(mexcs_orders, results):
        try:
            r_json = result.json()
            if r_json.get("success"):
                new_order_id = r_json.get("data")
                new_orders.append((mexc, new_order_id))
                print(f"  ✅ Ордер изменён: {old_order_id} → {new_order_id}")
            else:
                print(f"  ❌ Ошибка изменения ордера {old_order_id}: {r_json}")
        except Exception as e:
            print(f"  ❌ Ошибка парсинга ответа: {e}")

    return new_orders


async def cancel_limit_orders(mexcs_orders: list):
    """Отменить лимитные ордера на всех аккаунтах"""
    tasks = []
    print(f"🚫 Отменяем лимитные ордера на {len(mexcs_orders)} аккаунтах")

    for mexc, order_id in mexcs_orders:
        tasks.append(mexc.cancel_order([order_id]))

    results = await asyncio.gather(*tasks)
    return results


# Обновленная функция close_positions с использованием истории ордеров
async def close_positions(main_mexc, mexcs: list[Mexc], symbol, side, leverage, vol, open_type):
    close_side = 4 if side == 1 else 2

    all_positions = await asyncio.gather(*[mexc.get_open_positions() for mexc in mexcs])

    close_tasks = []
    account_info = []

    # Добавляем главный аккаунт в обработку
    main_positions = await main_mexc.get_open_positions()
    for position in main_positions:
        if (position['symbol'] == symbol and
                position['side'] == side):
            position_id = position['positionId']
            position_vol = position['vol']
            position_leverage = position['leverage']
            position_open_type = position['openType']

            print(
                f"  🔒 Закрываем позицию {position_id} (leverage={position_leverage}, vol={position_vol}) на ГЛАВНОМ аккаунте")

            close_tasks.append(main_mexc.close_position(
                symbol, position_id, position_leverage, position_vol, close_side, position_open_type))

            account_info.append({
                'mexc': main_mexc,
                'symbol': symbol,
                'side': side,
                'is_main': True
            })

    for mexc, positions in zip(mexcs, all_positions):
        for position in positions:
            if (position['symbol'] == symbol and
                    position['side'] == side):
                position_id = position['positionId']
                position_vol = position['vol']
                position_leverage = position['leverage']
                position_open_type = position['openType']

                print(
                    f"  🔒 Закрываем позицию {position_id} (leverage={position_leverage}, vol={position_vol}) на ведомом аккаунте")

                close_tasks.append(mexc.close_position(
                    symbol, position_id, position_leverage, position_vol, close_side, position_open_type))

                account_info.append({
                    'mexc': mexc,
                    'symbol': symbol,
                    'side': side,
                    'is_main': False
                })

    if close_tasks:
        # Закрываем все позиции
        results = await asyncio.gather(*close_tasks)

        # Ждем немного для обновления данных
        await asyncio.sleep(3)

        # Получаем данные о марже и PNL из истории ордеров
        account_results = []

        for acc_info in account_info:
            mexc = acc_info['mexc']
            symbol = acc_info['symbol']
            side = acc_info['side']
            is_main = acc_info['is_main']

            try:
                # Получаем историю ордеров
                orders = await mexc.get_order_history(symbol, limit=10)

                # Ищем последний закрывающий ордер
                close_orders = [order for order in orders if order.get('side') in [2, 4] and order.get('state') == 3]

                # Ищем соответствующий открывающий ордер
                open_orders = [order for order in orders if order.get('side') in [1, 3] and order.get('state') == 3]

                margin = "N/A"
                pnl = "N/A"
                entry_price = "N/A"
                exit_price = "N/A"

                if close_orders and open_orders:
                    latest_close = close_orders[0]
                    latest_open = open_orders[0]

                    margin = latest_open.get('orderMargin', 'N/A')
                    pnl = latest_close.get('profit', 'N/A')
                    entry_price = latest_open.get('dealAvgPrice', latest_open.get('dealAvgPriceStr', 'N/A'))
                    exit_price = latest_close.get('dealAvgPrice', latest_close.get('dealAvgPriceStr', 'N/A'))

                account_results.append({
                    'is_main': is_main,
                    'margin': margin,
                    'pnl': pnl,
                    'entry_price': entry_price,
                    'exit_price': exit_price
                })

            except Exception as e:
                print(f"  ⚠️ Ошибка получения данных закрытия: {e}")
                account_results.append({
                    'is_main': is_main,
                    'margin': 'N/A',
                    'pnl': 'N/A',
                    'entry_price': 'N/A',
                    'exit_price': 'N/A'
                })

        # Формируем сообщение для Telegram с маржой и PNL
        side_text = "LONG" if side == 1 else "SHORT"
        message = f"✅ Закрытие позиции {symbol} side={side_text} x{leverage}\n\n"

        # Данные главного аккаунта
        main_data = next((acc for acc in account_results if acc['is_main']), None)
        if main_data:
            margin = main_data['margin']
            pnl = main_data['pnl']
            exit_price = main_data['exit_price']

            message += f"<b>Главный аккаунт:</b>\n"
            if margin != "N/A" and margin and pnl != "N/A" and pnl:
                pnl_float = float(pnl)
                pnl_sign = "+" if pnl_float >= 0 else ""
                if pnl_float >= 10.0:
                    message += f"  маржа=${float(margin):.2f},выход={exit_price}, PNL=☠️<code>{pnl_sign}{pnl_float:.4f}</code>☠️ \n"
                elif pnl_float <= -5.0:
                    message += f"  маржа=${float(margin):.2f},выход={exit_price}, PNL=🤡<code>{pnl_sign}{pnl_float:.4f}</code>🤡 \n"
                elif pnl_float >= 0.0:
                    message += f"  маржа=${float(margin):.2f},выход={exit_price}, PNL=🟢<code>{pnl_sign}{pnl_float:.4f}</code>🟢 \n"
                elif pnl_float < 0.0:
                    message += f"  маржа=${float(margin):.2f},выход={exit_price}, PNL=🔴<code>{pnl_sign}{pnl_float:.4f}</code>🔴 \n"

            else:
                message += f"  маржа=расчет недоступен, PNL=расчет недоступен, выход={exit_price}\n"

        message += f"\n<b>Ведомые аккаунты:</b>\n"

        # Данные ведомых аккаунтов
        slave_count = 1
        for acc_data in account_results:
            if not acc_data['is_main']:  # Только ведомые аккаунты
                margin = acc_data['margin']
                pnl = acc_data['pnl']
                exit_price = acc_data['exit_price']

                if margin != "N/A" and margin and pnl != "N/A" and pnl:
                    pnl_float = float(pnl)
                    pnl_sign = "+" if pnl_float >= 0 else ""
                    if pnl_float >= 10.0:
                        message += f"{slave_count}) маржа=${float(margin):.2f},выход={exit_price}, PNL=☠️<code>{pnl_sign}{pnl_float:.4f}</code>☠️ \n"
                    elif pnl_float <= -5.0:
                        message += f"{slave_count}) маржа=${float(margin):.2f},выход={exit_price}, PNL=🤡<code>{pnl_sign}{pnl_float:.4f}</code>🤡 \n"
                    else:
                        message += f"{slave_count}) маржа=${float(margin):.2f},выход={exit_price}, PNL=<code>{pnl_sign}{pnl_float:.4f}</code> \n"
                else:
                    message += f"{slave_count}) маржа=расчет недоступен, PNL=расчет недоступен, выход={exit_price}\n"
                slave_count += 1

        await send_telegram_message(message)
        return results
    return []


async def main():
    print("🟢 Бот запущен пользователем.")
    with open("./accounts/main.txt", "r") as file:
        for line in file:
            parts = line.strip().split("|")
            uid = parts[0]
            proxy = parts[1] if len(parts) > 1 else None
            main_mexc = Mexc(uid, proxy)

    accounts = load_accounts()
    accounts_mexc = []
    for account in accounts:
        accounts_mexc.append(Mexc(account[0], account[1]))
    slave_count = len(accounts_mexc)
    print(f"\nУспешно загружено {slave_count} ведомых аккаунтов\n")
    opened_positions = {}
    opened_orders = {}  # {orderId: {symbol, price, vol, leverage, side}}
    synced_orders = {}  # {main_orderId: [(mexc, acc_orderId), ...]}
    limit_positions = set()  # Позиции которые появились от исполнения лимитных ордеров

    while True:
        positions = await main_mexc.get_open_positions()
        # print(positions)

        current_position_ids = set(pos['positionId'] for pos in positions)

        closed_position_ids = [pos_id for pos_id in opened_positions.keys()
                               if pos_id not in current_position_ids]

        if closed_position_ids:
            close_tasks = []
            for closed_pos_id in closed_position_ids:
                pos_info = opened_positions[closed_pos_id]
                print(
                    f"❌ Позиция {closed_pos_id} закрыта на главном аккаунте, закрываем на всех остальных...")

                close_tasks.append(close_positions(
                    main_mexc,  # Добавляем главный аккаунт
                    accounts_mexc,
                    pos_info['symbol'],
                    pos_info['side'],
                    pos_info['leverage'],
                    pos_info['vol'],
                    pos_info['openType']
                ))

            await asyncio.gather(*close_tasks)

            for closed_pos_id in closed_position_ids:
                del opened_positions[closed_pos_id]
                print(f"✅ Позиция {closed_pos_id} закрыта на всех аккаунтах")

        new_positions = [
            pos for pos in positions if pos['positionId'] not in opened_positions]

        if new_positions:
            open_tasks = []
            for position in new_positions:
                positionId = position['positionId']
                symbol = position['symbol']
                side = position['side']
                vol = position['vol']
                leverage = position['leverage']
                stopLossPrice = position['stopLossPrice']
                openType = position['openType']

                # Проверяем - не появилась ли эта позиция от исполнения лимитного ордера
                position_key = (symbol, side)
                if position_key in limit_positions:
                    print(
                        f"⏭️ Позиция {positionId} ({symbol}, side={side}) появилась от лимитного ордера - пропускаем")
                    limit_positions.remove(position_key)

                    opened_positions[positionId] = {
                        'symbol': symbol,
                        'side': side,
                        'leverage': leverage,
                        'vol': vol,
                        'openType': openType
                    }
                    continue

                print(
                    f"🚀 Открываем позицию {positionId} ({symbol}, side={side}) для всех аккаунтов")

                open_tasks.append(open_positions(
                    main_mexc,
                    accounts_mexc, symbol, side, leverage, stopLossPrice, vol, openType))

                opened_positions[positionId] = {
                    'symbol': symbol,
                    'side': side,
                    'leverage': leverage,
                    'vol': vol,
                    'openType': openType
                }

            if open_tasks:
                all_results = await asyncio.gather(*open_tasks)

                for results in all_results:
                    for result in results:
                        print(result.json())

        # ========== ОБРАБОТКА ЛИМИТНЫХ ОРДЕРОВ ==========
        orders = await main_mexc.get_open_orders()
        current_order_ids = set(order['orderId'] for order in orders)

        # 1. ОБРАБОТКА УДАЛЁННЫХ/ИСПОЛНЕННЫХ ОРДЕРОВ
        removed_order_ids = [order_id for order_id in opened_orders.keys()
                             if order_id not in current_order_ids]

        if removed_order_ids:
            for removed_order_id in removed_order_ids:
                order_info = opened_orders[removed_order_id]
                symbol = order_info['symbol']
                side = order_info['side']
                leverage = order_info['leverage']

                # Проверяем - появилась ли позиция (исполнился ордер)
                position_exists = any(
                    pos['symbol'] == symbol and
                    pos['side'] == side
                    for pos in positions
                )

                if position_exists:
                    print(
                        f"✅ Лимитный ордер {removed_order_id} исполнился на главном аке ({symbol}, side={side})")
                    # Помечаем что позиция появилась от лимитки - не открывать по маркету
                    limit_positions.add((symbol, side))
                else:
                    # Ордер отменён пользователем - отменяем на всех аках
                    print(
                        f"🚫 Лимитный ордер {removed_order_id} отменён на главном аке - отменяем на всех")
                    if removed_order_id in synced_orders:
                        await cancel_limit_orders(synced_orders[removed_order_id])
                        del synced_orders[removed_order_id]

                del opened_orders[removed_order_id]

        # 2. ОБРАБОТКА НОВЫХ ОРДЕРОВ
        new_orders = [
            order for order in orders if order['orderId'] not in opened_orders]

        if new_orders:
            for order in new_orders:
                order_id = order['orderId']
                symbol = order['symbol']
                side = order['side']
                vol = order['vol']
                leverage = order['leverage']
                price = str(order['price'])
                openType = order['openType']

                print(
                    f"📝 Новый лимитный ордер {order_id} ({symbol}, side={side}, price={price}, openType={openType})")

                # Открываем на всех аках
                results = await open_limit_orders(
                    accounts_mexc, symbol, side, leverage, price, vol, openType)

                # Сохраняем связь
                acc_orders = []
                for mexc, result in zip(accounts_mexc, results):
                    try:
                        r_json = result.json()
                        if r_json.get("success"):
                            acc_order_id = r_json['data']['orderId']
                            acc_orders.append((mexc, acc_order_id))
                            print(f"  ✅ Ордер создан: {acc_order_id}")
                        else:
                            print(f"  ❌ Ошибка создания ордера: {r_json}")
                    except Exception as e:
                        print(f"  ❌ Ошибка парсинга: {e}")

                synced_orders[order_id] = acc_orders
                opened_orders[order_id] = {
                    'symbol': symbol,
                    'price': price,
                    'vol': vol,
                    'leverage': leverage,
                    'side': side,
                    'openType': openType
                }

        # 3. ОБРАБОТКА ИЗМЕНЁННЫХ ОРДЕРОВ
        # Ищем по (symbol, leverage, side) ордера с изменившимся orderId или параметрами
        for order in orders:
            order_id = order['orderId']
            if order_id in opened_orders:
                # Проверяем изменились ли параметры
                old_order = opened_orders[order_id]
                new_price = str(order['price'])
                new_vol = order['vol']

                if old_order['price'] != new_price or old_order['vol'] != new_vol:
                    print(
                        f"✏️ Ордер {order_id} изменён (price: {old_order['price']}→{new_price}, vol: {old_order['vol']}→{new_vol})")

                    # Изменяем на всех аках
                    if order_id in synced_orders:
                        new_acc_orders = await change_limit_orders(
                            synced_orders[order_id], new_price, new_vol)
                        synced_orders[order_id] = new_acc_orders

                    # Обновляем информацию
                    opened_orders[order_id]['price'] = new_price
                    opened_orders[order_id]['vol'] = new_vol

        await asyncio.sleep(DELAY_BETWEEN_CHECK_POSITIONS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🔴 Бот остановлен пользователем.")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
