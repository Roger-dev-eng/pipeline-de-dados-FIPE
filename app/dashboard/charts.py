import plotly.express as px


COLOR_SEQUENCE = ["#2563eb", "#059669", "#ea580c", "#7c3aed", "#dc2626", "#0891b2"]


def apply_chart_layout(fig, height):
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 20, "b": 8},
        font={"family": "Inter, Segoe UI, sans-serif", "size": 13},
        hoverlabel={"font_size": 13},
        xaxis={"showgrid": True, "zeroline": False},
        yaxis={"showgrid": False, "zeroline": False},
    )
    return fig


def price_by_brand(df, limit=15):
    grouped = (
        df.groupby("marca", as_index=False)["valor"]
        .mean()
        .sort_values("valor", ascending=False)
        .head(limit)
    )
    fig = px.bar(
        grouped,
        x="valor",
        y="marca",
        orientation="h",
        labels={"valor": "Preco medio (R$)", "marca": "Marca"},
        color_discrete_sequence=[COLOR_SEQUENCE[0]],
    )
    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    return apply_chart_layout(fig, 430)


def price_by_year(df):
    grouped = (
        df.groupby("ano_modelo", as_index=False)["valor"]
        .mean()
        .sort_values("ano_modelo")
    )
    fig = px.line(
        grouped,
        x="ano_modelo",
        y="valor",
        markers=True,
        labels={"ano_modelo": "Ano modelo", "valor": "Preco medio (R$)"},
        color_discrete_sequence=[COLOR_SEQUENCE[1]],
    )
    return apply_chart_layout(fig, 360)


def price_by_fuel(df):
    grouped = (
        df.groupby("combustivel", as_index=False)["valor"]
        .mean()
        .sort_values("valor", ascending=False)
    )
    fig = px.bar(
        grouped,
        x="combustivel",
        y="valor",
        labels={"combustivel": "Combustivel", "valor": "Preco medio (R$)"},
        color="combustivel",
        color_discrete_sequence=COLOR_SEQUENCE,
    )
    fig.update_layout(showlegend=False)
    return apply_chart_layout(fig, 340)


def price_distribution(df):
    fig = px.histogram(
        df,
        x="valor",
        nbins=40,
        labels={"valor": "Preco FIPE (R$)"},
        color_discrete_sequence=[COLOR_SEQUENCE[5]],
    )
    return apply_chart_layout(fig, 340)
