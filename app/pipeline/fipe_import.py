import json
import os
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime

import pandas as pd
import requests
from requests.adapters import HTTPAdapter, Retry
from sqlalchemy import text

from app.db.engine import engine

_CACHE_PATH = os.getenv("FIPE_CACHE_PATH", "logs/fipe_cache.json")
_MAX_WORKERS = int(os.getenv("FIPE_MAX_WORKERS", "10"))
_TIMEOUT = int(os.getenv("FIPE_TIMEOUT", "10"))

_cache = {}
_cache_dirty = False
_cache_lock = threading.Lock()
_thread_local = threading.local()

retry_strategy = Retry(
    total=2,
    backoff_factor=0.3,
    status_forcelist=[500, 502, 503, 504],
    # Report rate limits immediately instead of sleeping invisibly for Retry-After.
    respect_retry_after_header=False,
)


def _get_session():
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


def _load_cache():
    global _cache
    if not os.path.exists(_CACHE_PATH):
        _cache = {}
        return
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as cache_file:
            _cache = json.load(cache_file)
    except (json.JSONDecodeError, OSError):
        _cache = {}


def _save_cache():
    global _cache_dirty
    with _cache_lock:
        if not _cache_dirty:
            return
        data = dict(_cache)
        _cache_dirty = False
    cache_dir = os.path.dirname(_CACHE_PATH)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    with open(_CACHE_PATH, "w", encoding="utf-8") as cache_file:
        json.dump(data, cache_file, ensure_ascii=True)


def _cache_get(key):
    with _cache_lock:
        return _cache.get(key)


def _cache_set(key, value):
    global _cache_dirty
    with _cache_lock:
        _cache[key] = value
        _cache_dirty = True


_load_cache()


def _emit(progress_callback, event, message, **data):
    print(message)
    if callable(progress_callback):
        progress_callback({
            "event": event,
            "message": message,
            **data,
        })


def _request_json(url):
    resposta = _get_session().get(url, timeout=_TIMEOUT)
    if resposta.status_code == 429:
        retry_after = resposta.headers.get("Retry-After")
        hint = f" Prazo informado pela API (Retry-After): {retry_after}." if retry_after else ""
        raise RuntimeError(
            "A API FIPE limitou as consultas (HTTP 429). "
            "Aguarde antes de iniciar outra coleta." + hint
        )
    resposta.raise_for_status()
    return resposta.json()


def _await_api(executor, operation, args, registros, limite, progress_callback):
    future = executor.submit(operation, *args)
    while True:
        done, _ = wait([future], timeout=2, return_when=FIRST_COMPLETED)
        if done:
            return future.result()
        _emit(
            progress_callback, "api_wait",
            f"Aguardando API FIPE: {operation.__name__} {args}; "
            f"{len(registros)}/{limite} registros coletados",
            current=len(registros), total=limite,
        )



def obter_marcas():
    url = "https://parallelum.com.br/fipe/api/v1/carros/marcas"
    cache_key = "marcas"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        dados = _request_json(url)
        if isinstance(dados, list) and all(isinstance(m, dict) for m in dados):
            _cache_set(cache_key, dados)
            return dados
        print(" Retorno inesperado da API de marcas.")
        return []
    except Exception as e:
        raise RuntimeError(f"Erro ao obter marcas: {e}") from e



def obter_modelos(codigo_marca):
    url = f"https://parallelum.com.br/fipe/api/v1/carros/marcas/{codigo_marca}/modelos"
    cache_key = f"modelos:{codigo_marca}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        dados = _request_json(url).get("modelos", [])
        _cache_set(cache_key, dados)
        return dados
    except Exception as e:
        raise RuntimeError(f"Erro ao obter modelos da marca {codigo_marca}: {e}") from e



def obter_anos(codigo_marca, codigo_modelo):
    url = f"https://parallelum.com.br/fipe/api/v1/carros/marcas/{codigo_marca}/modelos/{codigo_modelo}/anos"
    cache_key = f"anos:{codigo_marca}:{codigo_modelo}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        dados = _request_json(url)
        _cache_set(cache_key, dados)
        return dados
    except Exception as e:
        raise RuntimeError(f"Erro ao obter anos [{codigo_marca}/{codigo_modelo}]: {e}") from e



def obter_detalhes(codigo_marca, codigo_modelo, codigo_ano):
    url = f"https://parallelum.com.br/fipe/api/v1/carros/marcas/{codigo_marca}/modelos/{codigo_modelo}/anos/{codigo_ano}"
    cache_key = f"detalhes:{codigo_marca}:{codigo_modelo}:{codigo_ano}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        dados = _request_json(url)
        _cache_set(cache_key, dados)
        return dados
    except Exception as e:
        raise RuntimeError(f"Erro ao obter detalhes: {e}") from e



def _coletar_detalhe(cod_marca, nome_marca, cod_modelo, nome_modelo, cod_ano):
    try:
        detalhe = obter_detalhes(cod_marca, cod_modelo, cod_ano)
        if not detalhe:
            return None

        ano_modelo = detalhe.get("AnoModelo")
        if isinstance(ano_modelo, int):
            if not (1900 <= ano_modelo <= datetime.now().year):
                ano_modelo = None
        else:
            ano_modelo = None

        return {
            "marca": nome_marca,
            "modelo": nome_modelo,
            "ano_modelo": ano_modelo,
            "combustivel": detalhe.get("Combustivel"),
            "valor_str": detalhe.get("Valor"),
            "valor": _limpar_valor(detalhe.get("Valor")),
            "codigo_fipe": detalhe.get("CodigoFipe"),
            "sigla_combustivel": detalhe.get("SiglaCombustivel"),
            "data_consulta": detalhe.get("DataConsulta")
        }
    except Exception as e:
        raise RuntimeError(f"Erro na coleta de {nome_marca} {nome_modelo}: {e}") from e


def _drain_futures(futures, registros, limite_registros, progress_callback=None):
    done, _ = wait(futures, timeout=2, return_when=FIRST_COMPLETED)
    if not done:
        _emit(
            progress_callback, "api_wait",
            f"Aguardando {len(futures)} consultas da API; "
            f"{len(registros)}/{limite_registros} registros coletados",
            current=len(registros), total=limite_registros,
        )
    for future in done:
        futures.remove(future)
        resultado = future.result()
        if resultado:
            registros.append(resultado)
            if len(registros) % 10 == 0:
                print(f" Registros coletados: {len(registros)}", end="\r")
                if callable(progress_callback):
                    progress_callback({
                        "event": "records",
                        "message": f"{len(registros)} registros coletados",
                        "current": len(registros),
                        "total": limite_registros,
                    })
            if len(registros) >= limite_registros:
                return True
    return False


def coletar_dados_fipe(limite_registros=600, progress_callback=None):
    if limite_registros <= 0:
        raise ValueError("O limite de registros deve ser maior que zero")
    registros = []
    futures = []
    _emit(
        progress_callback, "collect_start",
        f"Coletando dados da API FIPE (limite: {limite_registros})",
        current=0, total=limite_registros,
    )
    try:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor, ThreadPoolExecutor(max_workers=1) as metadata:
            def consultar(operation, *args):
                return _await_api(metadata, operation, args, registros, limite_registros, progress_callback)

            def tarefas():
                marcas = consultar(obter_marcas)
                for marca_index, marca in enumerate(marcas, start=1):
                    cod_marca, nome_marca = marca.get("codigo"), marca.get("nome")
                    _emit(
                        progress_callback, "brand",
                        f"Processando marca {marca_index}/{len(marcas)}: {nome_marca}",
                        current=len(registros), total=limite_registros, brand=nome_marca,
                    )
                    for modelo in consultar(obter_modelos, cod_marca):
                        cod_modelo, nome_modelo = modelo["codigo"], modelo["nome"]
                        _emit(
                            progress_callback, "model",
                            f"Consultando anos de {nome_marca} {nome_modelo}; "
                            f"{len(registros)}/{limite_registros} registros coletados",
                            current=len(registros), total=limite_registros,
                        )
                        for ano in consultar(obter_anos, cod_marca, cod_modelo):
                            cod_ano = ano["codigo"] if isinstance(ano, dict) else ano
                            yield cod_marca, nome_marca, cod_modelo, nome_modelo, cod_ano

            try:
                for args in tarefas():
                    futures.append(executor.submit(_coletar_detalhe, *args))
                    while len(futures) >= _MAX_WORKERS:
                        if _drain_futures(futures, registros, limite_registros, progress_callback):
                            break
                    if len(registros) >= limite_registros:
                        break
                while futures and len(registros) < limite_registros:
                    _drain_futures(futures, registros, limite_registros, progress_callback)
            finally:
                for future in futures:
                    future.cancel()
                if any(not future.done() for future in futures):
                    _emit(
                        progress_callback, "api_wait",
                        "Encerrando consultas em andamento antes de finalizar a coleta; "
                        f"{len(registros)}/{limite_registros} registros coletados",
                        current=len(registros), total=limite_registros,
                    )
    finally:
        # Workers must finish before their newly fetched responses are persisted.
        _save_cache()

    reached_limit = len(registros) >= limite_registros
    _emit(
        progress_callback, "collect_limit" if reached_limit else "collect_done",
        f"Coleta encerrada: {len(registros)}/{limite_registros} registros coletados",
        current=len(registros), total=limite_registros,
    )
    return pd.DataFrame(registros)


def _limpar_valor(valor):
    if not valor:
        return None
    try:
        limpo = valor.replace("R$", "").replace(".", "").replace(",", ".").strip()
        return float(limpo)
    except (ValueError, AttributeError):
        return None

def salvar_no_banco(df, progress_callback=None):
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fipe_carros (
            id SERIAL PRIMARY KEY,
            marca VARCHAR(100),
            modelo VARCHAR(150),
            ano_modelo INTEGER,
            combustivel VARCHAR(50),
            valor_str VARCHAR(20),
            valor FLOAT,
            codigo_fipe VARCHAR(20),
            sigla_combustivel VARCHAR(10),
            data_consulta DATE DEFAULT CURRENT_DATE,
            CONSTRAINT unique_fipe UNIQUE (codigo_fipe, ano_modelo, combustivel)
        );
        """))

        conn.execute(text("""
        ALTER TABLE fipe_carros
        ALTER COLUMN data_consulta SET DEFAULT CURRENT_DATE
        """))

        if df.empty:
            _emit(progress_callback, "save_empty", "Nenhum dado coletado. Tabela garantida.")
            return {
                "collected": 0,
                "valid": 0,
                "inserted": 0,
                "existing": 0,
            }

        collected_count = len(df)
        df = df.copy()
        df["ano_modelo"] = df["ano_modelo"].apply(
            lambda x: int(x) if pd.notna(x) else None
        )
        df["valor"] = df["valor"].where(pd.notna(df["valor"]), None)

        df = df[
            df["codigo_fipe"].notna() &
            df["ano_modelo"].notna() &
            df["valor"].notna()
        ]

        if df.empty:
            _emit(progress_callback, "save_empty", "Nenhum dado valido apos filtros.")
            return {
                "collected": collected_count,
                "valid": 0,
                "inserted": 0,
                "existing": 0,
            }

        batch_size = 100
        total_inserido = 0
        total_validos = len(df)
        total_batches = (len(df) + batch_size - 1) // batch_size
        batch_failures = []

        insert_sql = text("""
        INSERT INTO fipe_carros (
            marca, modelo, ano_modelo, combustivel,
            valor_str, valor, codigo_fipe,
            sigla_combustivel
        ) VALUES (
            :marca, :modelo, :ano_modelo, :combustivel,
            :valor_str, :valor, :codigo_fipe,
            :sigla_combustivel
        )
        ON CONFLICT (codigo_fipe, ano_modelo, combustivel)
        DO NOTHING
        """)

        _emit(
            progress_callback,
            "save_start",
            f"Salvando {len(df)} registros em {total_batches} batches",
            current=0,
            total=total_batches,
        )

        for i in range(0, len(df), batch_size):
            batch = df.iloc[i:i + batch_size]
            batch_number = i // batch_size + 1
            try:
                result = conn.execute(insert_sql, batch.to_dict(orient="records"))
                inserted_batch = result.rowcount or 0
                total_inserido += inserted_batch
                print(f" Batch {batch_number}: {len(batch)} registros processados", end="\r")
                if callable(progress_callback):
                    progress_callback({
                        "event": "save_batch",
                        "message": (
                            f"Batch {batch_number}/{total_batches}: "
                            f"{inserted_batch} novos, {len(batch) - inserted_batch} ja existentes"
                        ),
                        "current": batch_number,
                        "total": total_batches,
                        "inserted": total_inserido,
                        "existing": (i + len(batch)) - total_inserido,
                    })
            except Exception as e:
                batch_failures.append((batch_number, e))
                _emit(
                    progress_callback,
                    "save_error",
                    f"Erro no batch {batch_number}: {e}",
                    current=batch_number,
                    total=total_batches,
                )
                continue

        if batch_failures:
            primeiro_batch, primeiro_erro = batch_failures[0]
            raise RuntimeError(
                f"Falha ao salvar {len(batch_failures)} batch(s) na tabela fipe_carros; "
                f"primeiro erro no batch {primeiro_batch}: {primeiro_erro}"
            )

        _emit(
            progress_callback,
            "save_done",
            (
                f"Insercao finalizada: {total_inserido} novos, "
                f"{total_validos - total_inserido} ja existentes"
            ),
            current=total_batches,
            total=total_batches,
            collected=collected_count,
            valid=total_validos,
            inserted=total_inserido,
            existing=total_validos - total_inserido,
        )
        return {
            "collected": collected_count,
            "valid": total_validos,
            "inserted": total_inserido,
            "existing": total_validos - total_inserido,
        }


def importar_dados_fipe(limite_registros=None, progress_callback=None):
    """Funcao principal: coleta e salva dados da FIPE."""
    if limite_registros is None:
        limite_registros = int(os.getenv("RECORDS_LIMIT", "600"))
    _emit(progress_callback, "start", "Pipeline FIPE iniciado")
    df = coletar_dados_fipe(limite_registros, progress_callback)
    summary = salvar_no_banco(df, progress_callback)
    _emit(
        progress_callback,
        "done",
        "Pipeline FIPE concluido com sucesso",
        current=limite_registros,
        total=limite_registros,
        summary=summary,
    )
    return summary


if __name__ == "__main__":
    importar_dados_fipe()
