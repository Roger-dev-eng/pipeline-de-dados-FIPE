import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.dashboard.charts import (
    price_by_brand,
    price_by_fuel,
    price_by_year,
    price_distribution,
)
from app.dashboard.queries import get_engine, load_fipe_data, table_exists


st.set_page_config(
    page_title="Dashboard FIPE",
    layout="wide",
)


@st.cache_resource(show_spinner=False)
def cached_engine():
    return get_engine()


@st.cache_data(ttl=300, show_spinner="Carregando dados da FIPE...")
def cached_data():
    engine = cached_engine()
    if not table_exists(engine):
        return pd.DataFrame()
    return load_fipe_data(engine)


def format_currency(value):
    if pd.isna(value):
        return "-"
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def render_page_style():
    st.markdown(
        """
        <style>
            .block-container {
                padding-top: 1.5rem;
                padding-bottom: 2rem;
                max-width: 1280px;
            }

            [data-testid="stSidebar"] {
                border-right: 1px solid color-mix(in srgb, currentColor 18%, transparent);
            }

            .page-header {
                padding: 0.2rem 0 1.1rem 0;
                border-bottom: 1px solid color-mix(in srgb, currentColor 18%, transparent);
                color: inherit;
                margin-bottom: 1.1rem;
            }

            .page-header h1 {
                font-size: 2rem;
                line-height: 1.15;
                margin: 0 0 0.35rem 0;
                letter-spacing: 0;
            }

            .page-header p {
                margin: 0;
                color: inherit;
                opacity: 0.75;
                font-size: 0.98rem;
            }

            .kpi-card {
                border: 1px solid color-mix(in srgb, currentColor 18%, transparent);
                border-radius: 8px;
                background: transparent;
                padding: 1rem 1.05rem;
                min-height: 104px;
            }

            .kpi-label {
                color: inherit;
                opacity: 0.7;
                font-size: 0.78rem;
                font-weight: 700;
                letter-spacing: 0.04em;
                text-transform: uppercase;
                margin-bottom: 0.45rem;
            }

            .kpi-value {
                color: inherit;
                font-size: 1.55rem;
                font-weight: 760;
                line-height: 1.15;
                letter-spacing: 0;
            }

            .section-title {
                color: inherit;
                font-size: 1.05rem;
                font-weight: 720;
                margin: 0.3rem 0 0.65rem 0;
            }

            div[data-testid="stMetric"] {
                border: 1px solid color-mix(in srgb, currentColor 18%, transparent);
                border-radius: 8px;
                padding: 1rem;
                background: transparent;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header():
    st.markdown(
        """
        <div class="page-header">
            <h1>Dashboard FIPE</h1>
            <p>Analise de precos, marcas, modelos e combustiveis a partir dos dados coletados da API FIPE.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpi(label, value):
    st.markdown(
        f"""
        <div class="kpi-card">
            <div class="kpi-label">{label}</div>
            <div class="kpi-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_pipeline_summary(summary):
    if not summary:
        return

    st.markdown('<div class="section-title">Resumo da ultima execucao</div>', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_kpi("Coletados", f"{summary.get('collected', 0):,}".replace(",", "."))
    with col2:
        render_kpi("Validos", f"{summary.get('valid', 0):,}".replace(",", "."))
    with col3:
        render_kpi("Novos inseridos", f"{summary.get('inserted', 0):,}".replace(",", "."))
    with col4:
        render_kpi("Ja existentes", f"{summary.get('existing', 0):,}".replace(",", "."))


def pipeline_progress_ratio(update):
    event = update.get("event")
    current = update.get("current") or 0
    total = update.get("total") or 1

    if event == "start":
        return 0.02
    if event in {"collect_start", "brand", "model", "api_wait", "records", "collect_limit", "collect_done"}:
        return min(0.78, 0.08 + (current / total) * 0.70)
    if event == "save_start":
        return 0.82
    if event == "save_batch":
        return min(0.97, 0.82 + (current / total) * 0.15)
    if event in {"save_done", "done"}:
        return 1.0
    return 0.5


def apply_filters(df):
    with st.sidebar:
        st.title("FIPE")
        st.caption("Filtros e coleta")
        st.divider()
        st.header("Filtros")

        brands = sorted(df["marca"].dropna().unique())
        selected_brands = st.multiselect("Marca", brands, default=brands)

        base = df[df["marca"].isin(selected_brands)] if selected_brands else df

        model_search = st.text_input(
            "Buscar modelo",
            placeholder="Ex.: Corolla",
            help="Busca parte do nome, sem diferenciar maiusculas de minusculas.",
        ).strip()

        fuels = sorted(base["combustivel"].dropna().unique())
        selected_fuels = st.multiselect("Combustivel", fuels, default=fuels)

        min_year = int(base["ano_modelo"].min())
        max_year = int(base["ano_modelo"].max())
        if min_year == max_year:
            st.caption(f"Ano modelo: {min_year}")
            selected_years = (min_year, max_year)
        else:
            selected_years = st.slider("Ano modelo", min_year, max_year, (min_year, max_year))

        max_rows = st.number_input("Linhas na tabela", min_value=10, max_value=500, value=100, step=10)

    filtered = base[
        base["combustivel"].isin(selected_fuels)
        & base["ano_modelo"].between(selected_years[0], selected_years[1])
    ]
    if model_search:
        filtered = filtered[
            filtered["modelo"].str.contains(model_search, case=False, regex=False, na=False)
        ]
    return filtered, max_rows


def render_pipeline_action():
    with st.container(border=True):
        st.markdown('<div class="section-title">Execucao da coleta FIPE</div>', unsafe_allow_html=True)
        st.caption("Acompanhe visualmente a chamada da API, o processamento dos registros e a gravacao no PostgreSQL.")

        control_col, info_col = st.columns([1, 2])
        with control_col:
            limit = st.number_input(
                "Limite de registros",
                min_value=10,
                max_value=5000,
                value=370,
                step=10,
                help="Controla quantos registros a coleta vai buscar antes de encerrar.",
            )
            run_pipeline = st.button("Iniciar coleta", type="primary", use_container_width=True)

        with info_col:
            st.info(
                "Use esta area para executar o ETL sem depender do terminal. "
                "Durante a execucao, o painel mostra marcas processadas, registros coletados e batches gravados."
            )

        if not run_pipeline:
            if "last_pipeline_summary" in st.session_state:
                st.caption(
                    "Ultima coleta concluida nesta sessao: "
                    + st.session_state["last_pipeline_completed_at"]
                )
                render_pipeline_summary(st.session_state["last_pipeline_summary"])
            return

        from app.pipeline.fipe_import import importar_dados_fipe

        progress_bar = st.progress(0)
        current_step = st.empty()
        log_output = st.empty()
        logs = []

        def on_progress(update):
            message = update.get("message", "")
            if message:
                logs.append(message)
            progress_bar.progress(pipeline_progress_ratio(update))
            current_step.info(message or "Executando pipeline...")
            log_output.code("\n".join(logs[-18:]), language="text")

        with st.status("Pipeline em execucao", expanded=True) as status:
            try:
                summary = importar_dados_fipe(limite_registros=int(limit), progress_callback=on_progress)
            except Exception as exc:
                status.update(label="Pipeline interrompido", state="error")
                current_step.error(str(exc))
                st.error("A coleta nao foi concluida. Verifique a mensagem acima antes de tentar novamente.")
                return
            cached_data.clear()
            status.update(label="Pipeline concluido", state="complete")

        completed_at = datetime.now(timezone(timedelta(hours=-3))).strftime(
            "%d/%m/%Y as %H:%M:%S (Brasilia)"
        )
        st.session_state["last_pipeline_summary"] = summary
        st.session_state["last_pipeline_completed_at"] = completed_at
        st.success("Coleta concluida. O painel foi atualizado com os dados disponiveis no banco.")
        st.caption("Ultima coleta concluida nesta sessao: " + completed_at)
        render_pipeline_summary(summary)


def render_empty_state():
    st.info(
        "A tabela fipe_carros ainda nao existe ou nao possui dados. "
        "Execute a coleta no painel acima para carregar os dados no PostgreSQL."
    )


def main():
    render_page_style()
    render_header()
    render_pipeline_action()

    try:
        df = cached_data()
    except Exception as exc:
        st.error(f"Nao foi possivel carregar os dados: {exc}")
        return

    if df.empty:
        render_empty_state()
        return

    latest_update = pd.to_datetime(df["data_consulta"], errors="coerce").max()
    if pd.notna(latest_update):
        st.caption(
            "Ultima atualizacao dos dados: "
            + latest_update.strftime("%d/%m/%Y")
            + " (data de insercao mais recente no banco)."
        )
    else:
        st.caption("Ultima atualizacao dos dados: data indisponivel.")

    df = df.dropna(subset=["valor", "ano_modelo"]).copy()
    if df.empty:
        render_empty_state()
        return
    df["ano_modelo"] = df["ano_modelo"].astype(int)

    filtered, max_rows = apply_filters(df)

    total_records = len(filtered)
    avg_price = filtered["valor"].mean()
    max_price = filtered["valor"].max()
    brands_count = filtered["marca"].nunique()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_kpi("Registros", f"{total_records:,}".replace(",", "."))
    with col2:
        render_kpi("Marcas", f"{brands_count:,}".replace(",", "."))
    with col3:
        render_kpi("Preco medio", format_currency(avg_price))
    with col4:
        render_kpi("Maior preco", format_currency(max_price))

    if filtered.empty:
        st.warning("Nenhum registro encontrado para os filtros selecionados.")
        return

    tab_overview, tab_distribution, tab_table = st.tabs(["Visao geral", "Distribuicao", "Dados"])

    with tab_overview:
        st.write("")
        left, right = st.columns([1.25, 1])
        with left:
            st.markdown('<div class="section-title">Preco medio por marca</div>', unsafe_allow_html=True)
            st.plotly_chart(price_by_brand(filtered), use_container_width=True)
        with right:
            st.markdown('<div class="section-title">Preco medio por combustivel</div>', unsafe_allow_html=True)
            st.plotly_chart(price_by_fuel(filtered), use_container_width=True)

        st.markdown('<div class="section-title">Evolucao do preco medio por ano</div>', unsafe_allow_html=True)
        st.plotly_chart(price_by_year(filtered), use_container_width=True)

    with tab_distribution:
        st.write("")
        st.markdown('<div class="section-title">Distribuicao de precos</div>', unsafe_allow_html=True)
        st.plotly_chart(price_distribution(filtered), use_container_width=True)

    with tab_table:
        st.write("")
        st.markdown('<div class="section-title">Registros filtrados</div>', unsafe_allow_html=True)
        visible_columns = [
            "marca",
            "modelo",
            "ano_modelo",
            "combustivel",
            "valor_str",
            "codigo_fipe",
            "data_consulta",
        ]
        st.dataframe(
            filtered[visible_columns].head(int(max_rows)),
            use_container_width=True,
            hide_index=True,
        )


if __name__ == "__main__":
    main()
