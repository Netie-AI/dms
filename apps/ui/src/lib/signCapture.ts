/** Signature strokes as SVG. The server stamps the PNG. Signing stays on the server. */

export type Point = { x: number; y: number };
export type Stroke = Point[];

export const SIGN_INTENT = "I agree to sign electronically";

export function strokesToSvg(strokes: Stroke[], width: number, height: number): string {
  const paths = strokes
    .filter((stroke) => stroke.length > 0)
    .map((stroke) => {
      const [first, ...rest] = stroke;
      const draw = [`M ${first.x.toFixed(1)} ${first.y.toFixed(1)}`];
      for (const point of rest) {
        draw.push(`L ${point.x.toFixed(1)} ${point.y.toFixed(1)}`);
      }
      return `<path d="${draw.join(" ")}" fill="none" stroke="#1a1a2e" stroke-width="2"/>`;
    })
    .join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${paths}</svg>`;
}
