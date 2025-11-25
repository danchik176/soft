import hashlib
import json
import time
from curl_cffi import AsyncSession


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

    async def place_order(self, obj):
        signature = self.mexc_crypto(self.cookies["u_id"], obj)
        headers = {
            'Content-Type': 'application/json',
            'x-mxc-sign': signature['sign'],
            'x-mxc-nonce': signature['time'],
            'Authorization': self.cookies["u_id"],
        }

        # Создаём новую сессию для каждого ордера и автоматически закрываем
        async with AsyncSession(
            proxy=self.proxy,
            headers=headers,
            cookies=self.cookies,
            impersonate="chrome136",
            verify=False
        ) as ses:
            r = await ses.post(
                "https://futures.mexc.com/api/v1/private/order/create", json=obj)
            return r

    async def open_position(self, symbol: str, side: int, leverage: int, stop_loss_price: str, vol: int):
        # ЛОНГ; МАРЖА 3$; пчеле 3x; позиция 11$; стоп лосс 101 454,1;

        # side; 1 - LONG; 3 - SHORT; 4 - CLOSE LONG; 2 - CLOSE SHORT;
        # openType; 1 - market;
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
            "openType": 2,
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

    async def close_position(self, symbol: str, position_id: int, leverage: int, vol: int, side: int):
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
            "openType": 1,
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

        # Не передаём параметры - они уже в self.ses
        r = await self.ses.get(url)
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

    async def get_history_positions(
            self,
            symbol: str = None,
            position_type: int = None,  # 1: long, 2: short
            start_time: int = None,
            end_time: int = None,
            page_num: int = 1,
            page_size: int = 100
    ):
        """📊 Получение исторических позиций"""
        url = "https://www.mexc.com/api/platform/futures/api/v1/private/position/list/history_positions"

        params = {
            "page_num": page_num,
            "page_size": page_size,
        }
        if symbol:
            params["symbol"] = symbol
        if position_type:
            params["position_type"] = position_type  # Используем position_type, как в API
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time

        r = await self.ses.get(url, params=params)
        r_json = r.json()

        if not r_json.get("success"):
            print(f"❌ Ошибка получения исторических позиций: {r_json.get('message', 'Unknown error')}")
            return []

        positions = r_json.get("data", [])

        # Форматируем аналогично open_positions
        formatted_positions = []
        for position in positions:
            position_type = position.get('positionType')
            formatted_position = {
                'positionId': position.get('positionId'),
                'symbol': position.get('symbol'),
                'side': 1 if position_type == 1 else 3,  # Конвертируем в ваш side
                'vol': position.get('holdVol'),
                'leverage': position.get('leverage'),
                'openAvgPrice': position.get('openAvgPrice'),
                'holdVol': position.get('holdVol'),
                'holdAvgPrice': position.get('holdAvgPrice'),
                'closeAvgPrice': position.get('closeAvgPrice'),  # Добавляем для exit_price
                'realised': position.get('realised'),  # PnL
                'closeProfitLoss': position.get('closeProfitLoss'),
                'state': position.get('state'),  # 3 = closed
                # Другие поля, если нужно
            }
            formatted_positions.append(formatted_position)

        return formatted_positions