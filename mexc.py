import hashlib
import json
import time
from curl_cffi import AsyncSession
import asyncio


class Mexc:
    def __init__(self, uid: str, proxy: str):
        self.proxy = proxy
        self.cookies = {
            'u_id': uid,
        }
        self.headers = {
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7,uk;q=0.6',
            'authorization': f'{uid}',
            'baggage': 'sentry-environment=production,sentry-release=v5.23.14__1707796cd0ff0bc66953f90dde439241542ba701,sentry-public_key=1693f35b10b540f19e1c27821cdd5a74,sentry-trace_id=0e2bbe3fc8a7429abe66a910609a8a13,sentry-sample_rate=0.01,sentry-sampled=false',
            'cache-control': 'no-cache',
            'language': 'English',
            'platform': 'H5-web',
            'pragma': 'akamai-x-cache-on',
            'priority': 'u=1, i',
            'referer': 'https://www.mexc.com/ru-RU/futures/BTC_USDT?type=linear_swap&lang=ru-RU',
            'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'sentry-trace': '0e2bbe3fc8a7429abe66a910609a8a13-9506722b9030c4d9-0',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            'x-language': 'ru-RU',
        }
        if self.proxy:
            proxy = f"socks5://{self.proxy}"
            self.ses = AsyncSession(
                proxy=proxy, headers=self.headers, cookies=self.cookies, impersonate="chrome136", verify=False
            )
        else:
            self.ses = AsyncSession(
                headers=self.headers, cookies=self.cookies, impersonate="chrome136", verify=False
            )

    def md5(self, value):
        return hashlib.md5(value.encode('utf-8')).hexdigest()

    def mexc_crypto(self, key, obj):
        date_now = str(int(time.time() * 1000))
        g = self.md5(key + date_now)[7:]
        s = json.dumps(obj, separators=(',', ':'))
        sign = self.md5(date_now + s + g)
        return {'time': date_now, 'sign': sign}

    async def get_order_history(self, symbol: str = None, limit: int = 10):
        """Получение истории ордеров"""
        url = "https://www.mexc.com/api/platform/futures/api/v1/private/order/list/history_orders"
        params = {
            'page_num': 1,
            'page_size': limit,
            'state': 3  # Выполненные ордера (state=3)
        }
        if symbol:
            params['symbol'] = symbol

        r = await self._request_with_retry('GET', url, params=params)
        r_json = r.json()

        if not r_json.get("success"):
            print(f"❌ Ошибка получения истории ордеров: {r_json.get('message', 'Unknown error')}")
            return []

        return r_json.get("data", [])
    async def _request_with_retry(self, method, url, max_retries=3, timeout=10, **kwargs):
        """Выполняет запрос с retry логикой и таймаутом"""
        for attempt in range(max_retries):
            try:
                if method.upper() == 'GET':
                    response = await self.ses.get(url, timeout=timeout, **kwargs)
                elif method.upper() == 'POST':
                    response = await self.ses.post(url, timeout=timeout, **kwargs)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                return response
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                    print(f"⚠️ Ошибка запроса (попытка {attempt + 1}/{max_retries}): {e}. Повтор через {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"❌ Все попытки запроса исчерпаны: {e}")
                    raise

    async def place_order(self, obj):
        signature = self.mexc_crypto(self.cookies["u_id"], obj)
        headers = {
            'Content-Type': 'application/json',
            'x-mxc-sign': signature['sign'],
            'x-mxc-nonce': signature['time'],
            'Authorization': self.cookies["u_id"],
        }

        # Создаём новую сессию для каждого ордера с retry логикой
        for attempt in range(3):
            try:
                async with AsyncSession(
                    proxy=self.proxy,
                    headers=headers,
                    cookies=self.cookies,
                    impersonate="chrome136",
                    verify=False
                ) as ses:
                    r = await ses.post(
                        "https://futures.mexc.com/api/v1/private/order/create",
                        json=obj,
                        timeout=10
                    )
                    return r
            except Exception as e:
                if attempt < 2:
                    wait_time = 2 ** attempt
                    print(f"⚠️ Ошибка place_order (попытка {attempt + 1}/3): {e}. Повтор через {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"❌ Все попытки place_order исчерпаны: {e}")
                    raise

    async def open_position(self, symbol: str, side: int, leverage: int, stop_loss_price: str, vol: int, open_type: int):
        # ЛОНГ; МАРЖА 3$; пчеле 3x; позиция 11$; стоп лосс 101 454,1;

        # side; 1 - LONG; 3 - SHORT; 4 - CLOSE LONG; 2 - CLOSE SHORT;
        # openType; 1 - isolated margin, 2 - cross margin;
        # type; 5 - хз что это;
        # vol; одна единица vol, равняется 11$; 10 vol = 110$;
        # leverage; кредитное плечо;
        # marketCeiling; хз что это;
        # stopLossPrice; цена стоп лосса;
        # lossTrend; хз что это;
        # priceProtect; хз что это;

        template = {
            'symbol': 'BTC_USDT',
            'side': 1,
            'openType': 1,
            'type': '5',
            'vol': 1,
            'leverage': 3,
            'marketCeiling': False,
            'stopLossPrice': '101454.1',
            'lossTrend': '1',
            'priceProtect': '0',
        }

        object = {
            "symbol": symbol,
            "side": side,
            "openType": open_type,
            "type": "5",
            "vol": vol,
            "leverage": leverage,
            "marketCeiling": False,
            "stopLossPrice": stop_loss_price,
            "lossTrend": "1",
            "priceProtect": "0",
        }

        # return {
        #     "success": true,
        #     "code": 0,
        #     "data": {
        #         "orderId": "737177575079625216",
        #         "ts": 1761427230000
        #     }
        # }

        return await self.place_order(object)

    async def open_position_limit(self, symbol: str, side: int, leverage: int, price: str, vol: int, open_type: int):
        # side; 1 - LONG; 3 - SHORT; 4 - CLOSE LONG; 2 - CLOSE SHORT;
        # openType; 1 - isolated margin, 2 - cross margin;
        # type; 1 - limit, 5 - market;
        # vol; одна единица vol, равняется 11$; 10 vol = 110$;
        # leverage; кредитное плечо;
        # price; цена ордера;
        template = {
            'symbol': 'BTC_USDT',
            'side': 1,
            'openType': 1,
            'type': 1,
            'vol': 8,
            'leverage': 5,
            'marketCeiling': False,
            'price': '90000',
            'priceProtect': '0',
        }

        object = {
            "symbol": symbol,
            "side": side,
            "openType": open_type,
            "type": 1,
            "vol": vol,
            "leverage": leverage,
            "marketCeiling": False,
            "price": price,
            "priceProtect": "0",
        }

        return await self.place_order(object)

    async def close_position(self, symbol: str, position_id: int, leverage: int, vol: int, side: int, open_type: int):
        # side; 1 - LONG; 3 - SHORT; 4 - CLOSE LONG; 2 - CLOSE SHORT;
        template = {
            'symbol': 'BTC_USDT',
            'openType': 1,
            'positionId': 1100856115,
            'leverage': 3,
            'type': 5,
            'vol': 1,
            'side': 4,
            'flashClose': True,
            'priceProtect': '0',
        }

        object = {
            "symbol": symbol,
            "openType": open_type,
            "positionId": position_id,
            "leverage": leverage,
            "type": 5,
            "vol": vol,
            "side": side,
            "flashClose": True,
            "priceProtect": "0",
        }
        return await self.place_order(object)

    async def get_open_positions(self, symbol: str = None):
        """📊 Получение открытых позиций через open_positions API"""
        # Используем новый эндпоинт для получения открытых позиций
        url = "https://www.mexc.com/api/platform/futures/api/v1/private/position/open_positions"

        # Используем retry логику
        r = await self._request_with_retry('GET', url)
        r_json = r.json()

        # print(f"📊 get_open_positions response: {r_json}")

        if not r_json.get("success"):
            print(
                f"❌ Ошибка получения позиций: {r_json.get('message', 'Unknown error')}")
            return []

        positions = r_json.get("data", [])

        # Структура позиции из open_positions:
        # {
        #     "positionId": 1105037265,
        #     "symbol": "BTC_USDT",
        #     "positionType": 1,
        #     "openType": 1,
        #     "state": 1,
        #     "holdVol": 1,
        #     "frozenVol": 0,
        #     "closeVol": 0,
        #     "holdAvgPrice": 114744.1,
        #     "openAvgPrice": 114744.1,
        #     "closeAvgPrice": 0,
        #     "liquidatePrice": 103333.9,
        #     "leverage": 10,
        #     "realised": -0.0091,
        #     "profitRatio": -0.0079,
        #     "closeProfitLoss": 0,
        #     ...
        # }

        # Преобразуем positionType в side для совместимости
        # positionType: 1 - LONG, 2 - SHORT
        # Наш side: 1 - LONG, 3 - SHORT
        formatted_positions = []
        for position in positions:
            position_type = position.get('positionType')

            formatted_position = {
                'positionId': position.get('positionId'),
                'symbol': position.get('symbol'),
                'side': 1 if position_type == 1 else 3,  # Конвертируем positionType в side
                'vol': position.get('holdVol'),
                'leverage': position.get('leverage'),
                'openType': position.get('openType'),
                'openAvgPrice': position.get('openAvgPrice'),
                'holdVol': position.get('holdVol'),
                'holdAvgPrice': position.get('holdAvgPrice'),
                'profit': position.get('closeProfitLoss', 0),
                'stopLossPrice': position.get('stopLossPrice'),
                'liquidatePrice': position.get('liquidatePrice'),
                'realised': position.get('realised'),
            }
            formatted_positions.append(formatted_position)

        return formatted_positions

    async def get_open_orders(self):
        url = "https://www.mexc.com/api/platform/futures/api/v1/private/order/list/open_orders?page_size=200"
        r = await self._request_with_retry('GET', url)
        r_json = r.json()

        if not r_json.get("success"):
            print(
                f"❌ Ошибка получения ордеров: {r_json.get('message', 'Unknown error')}")
            return []

        orders = r_json.get("data", [])
        return orders
        # {
        #     "success": true,
        #     "code": 0,
        #     "data": [
        #         {
        #             "orderId": "746397375190355968",
        #             "symbol": "BTC_USDT",
        #             "positionId": 0,
        #             "price": 90000,
        #             "priceStr": "90000",
        #             "vol": 7,
        #             "leverage": 5,
        #             "side": 1,
        #             "category": 1,
        #             "orderType": 1,
        #             "dealAvgPrice": 0,
        #             "dealAvgPriceStr": "0",
        #             "dealVol": 0,
        #             "orderMargin": 12.6504,
        #             "takerFee": 0,
        #             "makerFee": 0,
        #             "profit": 0,
        #             "feeCurrency": "USDT",
        #             "openType": 1,
        #             "state": 2,
        #             "externalOid": "_m_02ce823c004a4b21ae4c8c85da910231",
        #             "errorCode": 0,
        #             "usedMargin": 0,
        #             "createTime": 1763625401575,
        #             "updateTime": 1763625401652,
        #             "positionMode": 1,
        #             "version": 1,
        #             "showCancelReason": 0,
        #             "showProfitRateShare": 0,
        #             "bboTypeNum": 0,
        #             "totalFee": 0,
        #             "zeroSaveTotalFeeBinance": 0,
        #             "zeroTradeTotalFeeBinance": 0
        #         }
        #     ]
        # }

    async def cancel_order(self, order_ids: list):
        """Отмена ордеров по списку ID"""
        signature = self.mexc_crypto(self.cookies["u_id"], order_ids)
        headers = {
            'Content-Type': 'application/json',
            'x-mxc-sign': signature['sign'],
            'x-mxc-nonce': signature['time'],
            'Authorization': self.cookies["u_id"],
        }

        for attempt in range(3):
            try:
                async with AsyncSession(
                    proxy=self.proxy,
                    headers=headers,
                    cookies=self.cookies,
                    impersonate="chrome136",
                    verify=False
                ) as ses:
                    r = await ses.post(
                        "https://www.mexc.com/api/platform/futures/api/v1/private/order/cancel",
                        json=order_ids,
                        timeout=10
                    )
                    return r
            except Exception as e:
                if attempt < 2:
                    wait_time = 2 ** attempt
                    print(f"⚠️ Ошибка cancel_order (попытка {attempt + 1}/3): {e}. Повтор через {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"❌ Все попытки cancel_order исчерпаны: {e}")
                    raise

    async def change_limit_order(self, order_id: str, price: str, vol: int):
        """Изменение лимитного ордера. Возвращает новый orderId"""
        obj = {
            "orderId": order_id,
            "price": price,
            "vol": vol
        }

        signature = self.mexc_crypto(self.cookies["u_id"], obj)
        headers = {
            'Content-Type': 'application/json',
            'x-mxc-sign': signature['sign'],
            'x-mxc-nonce': signature['time'],
            'Authorization': self.cookies["u_id"],
        }

        for attempt in range(3):
            try:
                async with AsyncSession(
                    proxy=self.proxy,
                    headers=headers,
                    cookies=self.cookies,
                    impersonate="chrome136",
                    verify=False
                ) as ses:
                    r = await ses.post(
                        "https://www.mexc.com/api/platform/futures/api/v1/private/order/change_limit_order",
                        json=obj,
                        timeout=10
                    )
                    return r
            except Exception as e:
                if attempt < 2:
                    wait_time = 2 ** attempt
                    print(f"⚠️ Ошибка change_limit_order (попытка {attempt + 1}/3): {e}. Повтор через {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"❌ Все попытки change_limit_order исчерпаны: {e}")
                    raise
