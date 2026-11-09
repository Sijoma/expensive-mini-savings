import { LitElement, css, html, type PropertyValues } from "lit";
import { property } from "lit/decorators.js";
import * as echarts from "echarts/core";
import { BarChart, CustomChart, LineChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { cssVar } from "./theme";

echarts.use([
  BarChart,
  LineChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  MarkAreaComponent,
  CanvasRenderer,
]);

export type ChartOption = echarts.EChartsCoreOption;

/** Define a custom element once (the module can be loaded again after an update). */
export function define(name: string, cls: CustomElementConstructor): void {
  if (!customElements.get(name)) customElements.define(name, cls);
}

/** ECharts in a Lit element: follows the theme colors and the size of its container. */
export class EmsChart extends LitElement {
  @property({ attribute: false }) option?: ChartOption;
  @property({ type: Number }) height = 300;
  @property({ type: Boolean }) dark = false;

  private chart?: echarts.ECharts;
  private resizer?: ResizeObserver;

  static styles = css`
    :host {
      display: block;
      width: 100%;
    }
    div {
      width: 100%;
    }
  `;

  render() {
    return html`<div style="height:${this.height}px"></div>`;
  }

  protected firstUpdated(): void {
    const el = this.renderRoot.querySelector("div") as HTMLDivElement;
    this.chart = echarts.init(el, undefined, { renderer: "canvas" });
    this.resizer = new ResizeObserver(() => this.chart?.resize());
    this.resizer.observe(el);
    this.apply();
  }

  protected updated(changed: PropertyValues): void {
    if (changed.has("height")) this.chart?.resize();
    if (this.chart && (changed.has("option") || changed.has("dark"))) this.apply();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.resizer?.disconnect();
    this.chart?.dispose();
    this.chart = undefined;
  }

  connectedCallback(): void {
    super.connectedCallback();
    // re-attached after a tab switch: render again
    if (this.hasUpdated && !this.chart) this.firstUpdated();
  }

  private apply(): void {
    if (!this.chart || !this.option) return;
    const text = cssVar(this, "--primary-text-color", "#212121");
    const muted = cssVar(this, "--secondary-text-color", "#727272");
    const line = cssVar(this, "--divider-color", "rgba(128,128,128,0.25)");
    const bg = cssVar(this, "--card-background-color", "#fff");
    const axes = (a: unknown) =>
      (Array.isArray(a) ? a : a ? [a] : []).map((x: Record<string, any>) => ({
        ...x,
        axisLabel: { color: muted, ...x.axisLabel },
        nameTextStyle: { color: muted, ...x.nameTextStyle },
      }));
    const option = this.option as Record<string, unknown>;
    this.chart.setOption(
      {
        backgroundColor: "transparent",
        textStyle: { color: text, fontFamily: "inherit" },
        animation: false,
        ...option,
        tooltip: {
          backgroundColor: bg,
          borderColor: line,
          textStyle: { color: text },
          confine: true,
          ...(option.tooltip as object),
        },
        legend: option.legend ? { textStyle: { color: muted }, ...(option.legend as object) } : undefined,
        xAxis: axes(option.xAxis),
        yAxis: axes(option.yAxis),
      },
      { notMerge: true },
    );
  }
}
define("ems-chart", EmsChart);

/** Axis defaults that follow the theme. */
export function axisStyle(): Record<string, unknown> {
  return {
    axisLine: { lineStyle: { color: "rgba(128,128,128,0.4)" } },
    axisLabel: { hideOverlap: true },
    splitLine: { lineStyle: { color: "rgba(128,128,128,0.15)", type: "dashed" } },
  };
}
