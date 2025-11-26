import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Any

from api.mexc import Mexc
import config

# === Конфиг из config.py ===

DELAY_BETWEEN_CHECK_POSITIONS: float = getattr(
    config, "DELAY_BETWEEN_CHECK_POSITIONS", 0.3
)
ACCOUNTS_DIR: str = getattr(config, "ACCOUNTS_DIR", "./accounts")
MAIN_ACCOUNT_FILE: str = getattr(config, "MAIN_ACCOUNT_FILE", "main.txt")
MAX_CONCURRENT_REQUESTS: int = getattr(config, "MAX_CONCURRENT_REQUESTS", 5)

TELEGRAM_BOT_TOKEN: str = getattr(config, "TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = getattr(config, "TELEGRAM_CHAT_ID", "")

# "market" или "limit"
ORDER_TYPE: str = getattr(config, "ORDER_TYPE", "market")
# Смещение цены лимитки от цены входа главной позиции (например, -0.1)
LIMIT_ORDER_PRICE_OFFSET: float = getattr(
    config, "LIMIT_ORDER_PRICE_OFFSET", 0.0
)


# === Логгер ===

def setup_logger() -> logging.Logger:
    logger = logging.getLogger("trade_copier")
    if logger.handlers:
        return logger  # уже настроен

    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler("trade_copier.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()


# === Датаклассы ===

@dataclass
class Account:
    uid: str
    proxy: Optional[str] = None
    size_multiplier: float = 1.0
    mexc: Mexc = field(init=False)

    def __post_init__(self) -> None:
        self.mexc = Mexc(self.uid, self.proxy)


@dataclass
class FollowerPositionState:
    uid: str
    requested_vol: float
    position_id: Optional[str] = None
    entry_price: Optional[float] = None


@dataclass
class PositionSyncInfo:
    symbol: str
    side: int
    leverage: int
    main_entry_price: Optional[float]
    main_vol: float
    follower_positions: Dict[str, FollowerPositionState] = field(
        default_factory=dict
    )


# === Парсинг аккаунтов ===

def parse_account_line(line: str):
    """
    Формат строк:
      uid|proxy
      uid|proxy|multiplier
    proxy можно не указывать (uid или uid|)
    """
    parts = line.strip().split("|")
    if not parts or not parts[0].strip():
        return None

    uid = parts[0].strip()
    proxy = None
    if len(parts) > 1 and parts[1].strip():
        proxy = parts[1].strip()

    multiplier = 1.0
    if len(parts) > 2 and parts[2].strip():
        try:
            multiplier = float(parts[2].strip())
        except ValueError:
            logger.warning(
                "Не удалось распарсить multiplier '%s' для uid %s, использую 1.0",
                parts[2],
                uid,
            )

    return uid, proxy, multiplier


def load_main_account() -> Account:
    path = os.path.join(ACCOUNTS_DIR, MAIN_ACCOUNT_FILE)
    if not os.path.exists(path):
        raise RuntimeError(f"Не найден файл главного аккаунта: {path}")

    uid = proxy = None
    multiplier = 1.0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            parsed = parse_account_line(line)
            if not parsed:
                continue
            uid, proxy, multiplier = parsed
            break  # первая валидная строка

    if not uid:
        raise RuntimeError(f"В {path} нет ни одной валидной строки с аккаунтом")

    acc = Account(uid=uid, proxy=proxy, size_multiplier=multiplier)
    logger.info("Главный аккаунт: %s (proxy=%s)", acc.uid, acc.proxy)
    return acc


def load_follower_accounts() -> List[Account]:
    accounts: List[Account] = []
    if not os.path.isdir(ACCOUNTS_DIR):
        raise RuntimeError(f"Папка с аккаунтами не найдена: {ACCOUNTS_DIR}")

    for filename in os.listdir(ACCOUNTS_DIR):
        if filename == MAIN_ACCOUNT_FILE:
            continue
        filepath = os.path.join(ACCOUNTS_DIR, filename)
        if not os.path.isfile(filepath):
            continue

        with open(filepath, "r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                parsed = parse_account_line(line)
                if not parsed:
                    continue
                uid, proxy, multiplier = parsed
                accounts.append(Account(uid=uid, proxy=proxy, size_multiplier=multiplier))

    if not accounts:
        logger.warning("Не найдено ни одного ведомого аккаунта в %s", ACCOUNTS_DIR)
    else:
        logger.info("Загружено %d ведомых аккаунтов", len(accounts))

    return accounts


# === Вспомогательные функции ===

def _extract_first_float(data: dict, keys: List[str]) -> Optional[float]:
    """
    Пытаемся вытащить число из словаря по списку возможных ключей.
    Если по прямым ключам не нашли — пытаемся угадать по подстрокам
    (entry/open/price, exit/close/price, pnl/profit и т.д.).
    """
    # 1. Сначала пробуем строго по заданным ключам
    for key in keys:
        if key in data and data[key] is not None:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                continue

    # 2. Если не нашли — включаем "умную" эвристику по подстрокам
    lower_keys = [str(k).lower() for k in keys]
    fallback_substrings: List[str] = []

    # Определяем, что мы ищем: цену входа, выхода или PnL
    if any("pnl" in k or "profit" in k for k in lower_keys):
        # всё, что похоже на PnL / profit
        fallback_substrings = ["pnl", "profit"]
    elif any("exit" in k or "close" in k for k in lower_keys):
        # цена выхода
        fallback_substrings = ["exit", "close", "price"]
    elif any("entry" in k or "open" in k for k in lower_keys):
        # цена входа
        fallback_substrings = ["entry", "open", "price"]
    elif any("price" in k for k in lower_keys):
        # просто какая-то цена
        fallback_substrings = ["price"]

    if not fallback_substrings:
        return None

    # 3. Ищем по всем ключам словаря что-то подходящее по имени
    for k, v in data.items():
        if v is None:
            continue
        lk = str(k).lower()
        if any(sub in lk for sub in fallback_substrings):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue

    return None


def _extract_position_id(data: dict) -> Optional[str]:
    for key in ("positionId", "id", "orderId"):
        if key in data and data[key]:
            return str(data[key])
    return None


# === Основной класс копировщика ===

class TradeCopier:
    def __init__(self, main: Account, followers: List[Account]) -> None:
        self.main = main
        self.followers = followers
        self.opened_positions: Dict[str, PositionSyncInfo] = {}
        self.sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    # --- Telegram ---

    async def _send_telegram_message(self, text: str) -> None:
        """Отправка одного сообщения в Telegram (HTML-разметка)."""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return

        import urllib.request
        import urllib.error

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }

        def _do_request():
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
            except urllib.error.URLError as e:
                logger.error("Ошибка отправки сообщения в Telegram: %s", e)

        await asyncio.to_thread(_do_request)

    # --- Открытие на ведомых ---

    async def _open_on_follower(
        self,
        follower: Account,
        symbol: str,
        side: int,
        leverage: int,
        stop_loss_price: Optional[float],
        vol: float,
        main_entry_price: Optional[float],
        pos_info: PositionSyncInfo,
    ) -> Dict[str, Any]:
        async with self.sem:
            try:
                if ORDER_TYPE == "limit" and main_entry_price is not None:
                    limit_price = main_entry_price + LIMIT_ORDER_PRICE_OFFSET
                    try:
                        result = await follower.mexc.open_position(
                            symbol,
                            side,
                            leverage,
                            stop_loss_price,
                            vol,
                            order_type="limit",
                            price=limit_price,
                        )
                    except TypeError:
                        result = await follower.mexc.open_position(
                            symbol, side, leverage, stop_loss_price, vol
                        )
                else:
                    result = await follower.mexc.open_position(
                        symbol, side, leverage, stop_loss_price, vol
                    )
            except Exception as e:
                logger.exception(
                    "Ошибка открытия позиции на аккаунте %s: %s",
                    follower.uid,
                    e,
                )
                return {"status": "error", "uid": follower.uid, "error": str(e)}

        data = result.json() if hasattr(result, "json") else result
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {}

        if isinstance(data, dict):
            container = data.get("data") or data.get("result") or data
        else:
            container = {}

        follower_entry = _extract_first_float(
            container,
            [
                "entryPrice",
                "avgPrice",
                "price",
                "openPrice",
                "avgEntryPrice",
                "avgOpenPrice",
            ],
        )
        pos_id = _extract_position_id(container)

        # если цену входа не дали в ответе, пробуем взять из открытых позиций
        if follower_entry is None:
            try:
                async with self.sem:
                    positions = await follower.mexc.get_open_positions()
                for pos in positions:
                    try:
                        if (
                            pos.get("symbol") == symbol
                            and int(pos.get("side")) == side
                            and int(pos.get("leverage")) == leverage
                        ):
                            follower_entry = _extract_first_float(
                                pos,
                                [
                                    "entryPrice",
                                    "avgPrice",
                                    "price",
                                    "openPrice",
                                    "avgEntryPrice",
                                    "avgOpenPrice",
                                ],
                            )
                            break
                    except Exception:
                        continue
            except Exception as e:
                logger.warning("Ошибка получения позиций %s: %s", follower.uid, e)

        state = pos_info.follower_positions.get(follower.uid)
        if not state:
            state = FollowerPositionState(
                uid=follower.uid, requested_vol=vol, position_id=pos_id, entry_price=follower_entry
            )
            pos_info.follower_positions[follower.uid] = state
        else:
            state.position_id = pos_id
            state.entry_price = follower_entry

        return {
            "status": "ok",
            "uid": follower.uid,
            "entry_price": follower_entry,
            "requested_vol": vol,
        }

    # --- Закрытие на ведомых ---

    async def _close_on_follower(
        self,
        follower: Account,
        sync_info: PositionSyncInfo,
    ) -> Dict[str, Any]:
        """Закрываем все позиции на ведомом по symbol/side/leverage."""
        close_side = 4 if sync_info.side == 1 else 2
        position_type = 1 if sync_info.side == 1 else 2  # Для API: 1=long, 2=short

        try:
            async with self.sem:
                positions = await follower.mexc.get_open_positions()
        except Exception as e:
            logger.exception(
                "Ошибка получения позиций аккаунта %s при закрытии: %s",
                follower.uid,
                e,
            )
            return {"status": "error", "uid": follower.uid, "error": str(e)}

        to_close: List[dict] = []
        for pos in positions:
            try:
                if (
                    pos.get("symbol") == sync_info.symbol
                    and int(pos.get("side")) == sync_info.side
                    and int(pos.get("leverage")) == sync_info.leverage
                ):
                    to_close.append(pos)
            except Exception:
                continue

        if not to_close:
            return {"status": "no_position", "uid": follower.uid}

        results: List[dict] = []

        for pos in to_close:
            position_id = str(pos["positionId"])
            vol = float(pos["vol"])

            try:
                async with self.sem:
                    result = await follower.mexc.close_position(
                        sync_info.symbol,
                        position_id,
                        sync_info.leverage,
                        vol,
                        close_side,
                    )
            except Exception as e:
                logger.exception(
                    "Ошибка закрытия позиции %s на аккаунте %s: %s",
                    position_id,
                    follower.uid,
                    e,
                )
                results.append(
                    {
                        "position_id": position_id,
                        "status": "error",
                        "error": str(e),
                        "vol": vol,
                    }
                )
                continue

            # === Достаём ответ сразу (чтобы логгер всегда видел container) ===
            try:
                raw_data = result.json() if hasattr(result, "json") else result
                if isinstance(raw_data, str):
                    raw_data = json.loads(raw_data)
            except Exception as e:
                logger.warning("Не удалось распарсить ответ закрытия для %s: %s", follower.uid, e)
                raw_data = {}

            container = raw_data.get("data") or raw_data.get("result") or raw_data if isinstance(raw_data, dict) else {}
            logger.info("RAW CLOSE RESPONSE %s: %s", follower.uid, container)

            # Ждём, пока позиция попадёт в историю (1.5–3 сек обычно хватает)
            await asyncio.sleep(2)

            # Получаем историю за последний час
            now_ms = int(time.time() * 1000)
            start_time = now_ms - 3600_000  # 1 час назад
            end_time = now_ms

            exit_price = None
            pnl = None

            try:
                history = await follower.mexc.get_history_positions(
                    symbol=sync_info.symbol,
                    position_type=position_type,
                    start_time=start_time,
                    end_time=end_time,
                    page_size=50,
                )

                # Ищем нужную закрытую позицию
                for h in history:
                    if str(h.get("positionId")) == position_id and h.get("state") == 3:
                        exit_price = h.get("closeAvgPrice")
                        pnl = h.get("realised") or h.get("closeProfitLoss")
                        break
                else:
                    # Если по ID не нашли — ищем по совпадению vol + symbol + side
                    for h in history:
                        if (h.get("symbol") == sync_info.symbol and
                            h.get("side") == sync_info.side and
                            float(h.get("vol") or 0) == vol and
                            h.get("state") == 3):
                            exit_price = h.get("closeAvgPrice")
                            pnl = h.get("realised") or h.get("closeProfitLoss")
                            break

            except Exception as e:
                logger.warning("Ошибка получения истории позиций для %s: %s", follower.uid, e)

            # Если всё ещё нет — пробуем взять из ответа на ордер (редко, но бывает)
            if exit_price is None:
                exit_price = _extract_first_float(
                    container,
                    ["avgExitPrice", "exitPrice", "closeAvgPrice", "price", "closePrice"],
                )
            if pnl is None:
                pnl = _extract_first_float(
                    container,
                    ["realised", "realizedPnl", "pnl", "closeProfitLoss", "profit"],
                )

            results.append(
                {
                    "position_id": position_id,
                    "status": "ok",
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "vol": vol,
                }
            )

        return {"status": "ok", "uid": follower.uid, "positions": results}

    # --- Новые позиции на главном ---

    async def _handle_new_positions(self, positions: List[dict]) -> None:
        for position in positions:
            position_id = str(position["positionId"])
            symbol = position["symbol"]
            side = int(position["side"])
            leverage = int(position["leverage"])
            vol = float(position["vol"])
            stop_loss_price = position.get("stopLossPrice")
            if stop_loss_price is not None:
                stop_loss_price = float(stop_loss_price)

            main_entry_price = _extract_first_float(
                position,
                [
                    "entryPrice",
                    "avgPrice",
                    "price",
                    "openPrice",
                    "avgEntryPrice",
                    "avgOpenPrice",
                ],
            )

            logger.info(
                "Новая позиция на главном аккаунте: %s id=%s side=%s lev=%s vol=%s",
                symbol,
                position_id,
                side,
                leverage,
                vol,
            )

            sync_info = PositionSyncInfo(
                symbol=symbol,
                side=side,
                leverage=leverage,
                main_entry_price=main_entry_price,
                main_vol=vol,
            )
            self.opened_positions[position_id] = sync_info

            open_tasks: List[asyncio.Task] = []
            for follower in self.followers:
                follower_vol = vol * follower.size_multiplier
                open_tasks.append(
                    self._open_on_follower(
                        follower,
                        symbol,
                        side,
                        leverage,
                        stop_loss_price,
                        follower_vol,
                        main_entry_price,
                        sync_info,
                    )
                )

            if not open_tasks:
                continue

            results = await asyncio.gather(*open_tasks, return_exceptions=False)

            res_by_uid: Dict[str, dict] = {}
            for res in results:
                if isinstance(res, dict) and "uid" in res:
                    res_by_uid[res["uid"]] = res

            # --- Сообщение об открытии ---
            lines: List[str] = [
                f"🚀 <b>Открытие позиции</b> {symbol} side={side} x{leverage}",
                "",
            ]

            # Главный
            main_entry_display = (
                str(main_entry_price) if main_entry_price is not None else "н/д"
            )
            lines.append("<b>Главный аккаунт</b>:")
            lines.append(f"  Объём: {vol}")
            lines.append(f"  Вход: {main_entry_display}")

            # Ведомые
            lines.append("")
            lines.append("<b>Ведомые аккаунты:</b>")

            for idx, follower in enumerate(self.followers, start=1):
                uid = follower.uid
                state = sync_info.follower_positions.get(uid)
                res = res_by_uid.get(uid)

                if res and res.get("status") == "error":
                    lines.append(f"{idx}) ❌ ошибка открытия ({res.get('error')})")
                    continue

                if not state:
                    lines.append(f"{idx}) нет данных по позиции")
                    continue

                entry = state.entry_price
                f_vol = state.requested_vol

                entry_display = str(entry) if entry is not None else "н/д"
                line = f"{idx}) объём={f_vol}, вход={entry_display}"
                lines.append(line)

            msg = "\n".join(lines)
            await self._send_telegram_message(msg)

    # --- Закрытые позиции на главном ---

    # --- Вспомогательная функция для расчёта маржи ---
    def _calc_margin(self, vol: float, entry_price: float, leverage: int) -> str:
        """
        На MEXC для линейных фьючерсов (USDT-margined):
        Маржа = vol × entry_price / leverage
        vol — количество контрактов (например, для BTC_USDT 1 vol ≈ 0.0001 BTC)
        """
        if not entry_price or not leverage or leverage <= 0:
            return "—"
        try:
            margin = vol * entry_price / leverage
            margin/=10
            return f"<b>{margin:.4f}</b>"
        except:
            return "—"

    # === ЗАМЕНА МЕТОДА _handle_new_positions ===
    async def _handle_new_positions(self, positions: List[dict]) -> None:
        for position in positions:
            position_id = str(position["positionId"])
            symbol = position["symbol"]
            side = int(position["side"])
            leverage = int(position["leverage"])
            vol = float(position["vol"])
            stop_loss_price = position.get("stopLossPrice")
            if stop_loss_price is not None:
                stop_loss_price = float(stop_loss_price)

            main_entry_price = _extract_first_float(
                position,
                [
                    "entryPrice",
                    "avgPrice",
                    "price",
                    "openPrice",
                    "avgEntryPrice",
                    "avgOpenPrice",
                ],
            )

            logger.info(
                "Новая позиция на главном: %s id=%s side=%s lev=%s vol=%s",
                symbol, position_id, side, leverage, vol,
            )

            sync_info = PositionSyncInfo(
                symbol=symbol,
                side=side,
                leverage=leverage,
                main_entry_price=main_entry_price,
                main_vol=vol,
            )
            self.opened_positions[position_id] = sync_info

            open_tasks = []
            for follower in self.followers:
                follower_vol = vol * follower.size_multiplier
                open_tasks.append(
                    self._open_on_follower(
                        follower,
                        symbol,
                        side,
                        leverage,
                        stop_loss_price,
                        follower_vol,
                        main_entry_price,
                        sync_info,
                    )
                )

            if not open_tasks:
                continue

            results = await asyncio.gather(*open_tasks)
            res_by_uid = {r.get("uid"): r for r in results if isinstance(r, dict) and "uid" in r}

            # === КРАСИВОЕ СООБЩЕНИЕ ОБ ОТКРЫТИИ ===
            lines = [
                f"Открытие позиции <b>{symbol}</b> ×<b>{leverage}</b> "
                f"{'LONG' if side == 1 else 'SHORT'}",
                "",
            ]

            # Главный аккаунт
            main_margin = self._calc_margin(vol, main_entry_price or 0, leverage)
            entry_display = f"<b>{main_entry_price:.4f}</b>" if main_entry_price else "<b>—</b>"

            lines.append("<b>Главный аккаунт:</b>")
            lines.append(f"   Маржа: {main_margin} USDT")
            lines.append(f"   Цена входа: {entry_display}")

            lines.append("")
            lines.append("<b>Ведомые аккаунты:</b>")

            for idx, follower in enumerate(self.followers, start=1):
                uid = follower.uid
                state = sync_info.follower_positions.get(uid)
                res = res_by_uid.get(uid)

                if res and res.get("status") == "error":
                    lines.append(f"{idx}) Ошибка открытия: {res.get('error')}")
                    continue

                if not state or state.requested_vol is None:
                    lines.append(f"{idx}) Нет данных")
                    continue

                f_vol = state.requested_vol
                f_entry = state.entry_price
                f_margin = self._calc_margin(f_vol, f_entry or (main_entry_price or 0), leverage)
                f_entry_display = f"<b>{f_entry:.4f}</b>" if f_entry else "<b>—</b>"

                lines.append(f"{idx}) Маржа <b>{f_margin}</b> USDT | вход {f_entry_display}")

            msg = "\n".join(lines)
            await self._send_telegram_message(msg)

    # === ЗАМЕНА МЕТОДА _handle_closed_positions ===
    async def _handle_closed_positions(self, closed_ids: set) -> None:
        if not closed_ids:
            return

        index_by_uid = {f.uid: i for i, f in enumerate(self.followers, start=1)}

        for closed_pos_id in closed_ids:
            sync_info = self.opened_positions.get(closed_pos_id)
            if not sync_info:
                continue

            logger.info("Закрываем позицию %s (%s) на ведомых", closed_pos_id, sync_info.symbol)

            # Данные главного аккаунта
            main_exit_price = None
            main_pnl = None
            position_type = 1 if sync_info.side == 1 else 2

            await asyncio.sleep(2)

            now_ms = int(time.time() * 1000)
            start_time = now_ms - 3600_000
            end_time = now_ms

            try:
                history = await self.main.mexc.get_history_positions(
                    symbol=sync_info.symbol,
                    position_type=position_type,
                    start_time=start_time,
                    end_time=end_time,
                    page_size=50,
                )
                for h in history:
                    if str(h.get("positionId")) == closed_pos_id and h.get("state") == 3:
                        main_exit_price = _extract_first_float(h, ["closeAvgPrice", "avgClosePrice", "exitPrice"])
                        main_pnl = _extract_first_float(h, ["realised", "closeProfitLoss", "pnl", "profit"])
                        break
            except Exception as e:
                logger.warning("Ошибка получения истории главного: %s", e)

            main_margin = self._calc_margin(sync_info.main_vol, sync_info.main_entry_price or 0, sync_info.leverage)
            exit_display = f"<b>{main_exit_price:.4f}</b>" if main_exit_price else "<b>—</b>"
            pnl_display = f"<b>{main_pnl:+.4f}</b>" if main_pnl is not None else "<b>—</b>"

            close_tasks = [self._close_on_follower(f, sync_info) for f in self.followers]
            results = await asyncio.gather(*close_tasks)

            # === СООБЩЕНИЕ О ЗАКРЫТИИ ===
            lines = [
                f"Закрытие позиции <b>{sync_info.symbol}</b> ×<b>{sync_info.leverage}</b> "
                f"{'LONG' if sync_info.side == 1 else 'SHORT'}",
                "",
                "<b>Главный аккаунт:</b>",
                f"   Маржа: {main_margin} USDT",
                f"   Выход: {exit_display}",
                f"   PnL: {pnl_display} USDT",
                "",
                "<b>Ведомые аккаунты:</b>",
            ]

            for res in results:
                if not isinstance(res, dict):
                    continue
                uid = res.get("uid", "?")
                idx = index_by_uid.get(uid, "?")

                if res.get("status") != "ok" or not res.get("positions"):
                    lines.append(f"{idx}) Нет позиции / ошибка")
                    continue

                for p in res["positions"]:
                    if p.get("status") != "ok":
                        lines.append(f"{idx}) Ошибка закрытия")
                        continue

                    vol = p.get("vol", 0)
                    entry_price = sync_info.main_entry_price or 0
                    margin = self._calc_margin(vol, entry_price, sync_info.leverage)
                    exit_p = p.get("exit_price")
                    pnl = p.get("pnl")

                    exit_str = f"<b>{exit_p:.4f}</b>" if exit_p else "<b>—</b>"
                    pnl_str = f"<b>{pnl:+.4f}</b>" if pnl is not None else "<b>—</b>"

                    lines.append(f"{idx}) Маржа {margin} USDT | выход {exit_str} | PnL {pnl_str}")

            msg = "\n".join(lines)
            await self._send_telegram_message(msg)

            self.opened_positions.pop(closed_pos_id, None)

    # --- Один шаг цикла ---

    async def sync_once(self) -> None:
        try:
            positions = await self.main.mexc.get_open_positions()
        except Exception as e:
            logger.exception("Ошибка получения позиций главного аккаунта: %s", e)
            return

        current_ids = {str(pos["positionId"]) for pos in positions}
        known_ids = set(self.opened_positions.keys())

        closed_ids = known_ids - current_ids
        new_positions = [
            pos for pos in positions if str(pos["positionId"]) not in known_ids
        ]

        if closed_ids:
            await self._handle_closed_positions(closed_ids)
        if new_positions:
            await self._handle_new_positions(new_positions)

    # --- Основной цикл ---

    async def run(self) -> None:
        logger.info(
            "Старт копировщика. Задержка между проверками: %s сек",
            DELAY_BETWEEN_CHECK_POSITIONS,
        )
        while True:
            await self.sync_once()
            await asyncio.sleep(DELAY_BETWEEN_CHECK_POSITIONS)


# === Точка входа ===

async def main() -> None:
    main_acc = load_main_account()
    followers = load_follower_accounts()
    copier = TradeCopier(main_acc, followers)
    await copier.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем (Ctrl+C)")
