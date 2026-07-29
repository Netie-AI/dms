"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import AppShell from "../../components/AppShell";
import {
  ApiOfflineError,
  confirmItemDims,
  fetchLocationSpace,
  fetchWarehouseTree,
  intakeItem,
  scanMove,
  warehouseQrLabelUrl,
} from "../../lib/api";

function TreeNode({ node, depth = 0, selected, onSelect }) {
  const pad = { paddingLeft: `${depth * 14}px` };
  return (
    <div>
      <button
        type="button"
        className={`cx-warehouse-node ${selected === node.id ? "active" : ""}`}
        style={pad}
        onClick={() => onSelect(node)}
      >
        <span className="cx-mono">{node.kind.toUpperCase()}</span> {node.code}
        {node.items?.length ? ` (${node.items.length})` : ""}
      </button>
      {(node.children || []).map((c) => (
        <TreeNode
          key={c.id}
          node={c}
          depth={depth + 1}
          selected={selected}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

export default function WarehousePage() {
  const { data, mutate, isLoading } = useSWR("warehouse-tree", fetchWarehouseTree, {
    refreshInterval: 8000,
  });
  const tree = data?.tree || [];
  const [selected, setSelected] = useState(null);
  const [sku, setSku] = useState("");
  const [label, setLabel] = useState("");
  const [locationCode, setLocationCode] = useState("");
  const [photoFile, setPhotoFile] = useState(null);
  const [itemRef, setItemRef] = useState("");
  const [destQr, setDestQr] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingItem, setPendingItem] = useState(null);
  const [pendingDims, setPendingDims] = useState({ l: "", w: "", h: "", unit: "m" });
  const [spaceInfo, setSpaceInfo] = useState(null);

  const selectedItems = useMemo(() => selected?.items || [], [selected]);

  useEffect(() => {
    if (!selected?.id) {
      setSpaceInfo(null);
      return;
    }
    fetchLocationSpace(selected.id)
      .then(setSpaceInfo)
      .catch(() => setSpaceInfo(null));
  }, [selected?.id, data]);

  const fileToB64 = useCallback(
    (file) =>
      new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const raw = reader.result || "";
          const b64 = String(raw).split(",")[1] || "";
          resolve(b64);
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
      }),
    []
  );

  async function handleIntake(e) {
    e.preventDefault();
    if (!photoFile) {
      setStatus("Photo required for intake.");
      return;
    }
    setBusy(true);
    setStatus("");
    try {
      const photo = await fileToB64(photoFile);
      const res = await intakeItem({ sku, label, location_code: locationCode, photo });
      setPendingItem(res.item);
      const suggested = res.suggested_dims || {};
      setPendingDims({
        l: String(suggested.l ?? ""),
        w: String(suggested.w ?? ""),
        h: String(suggested.h ?? ""),
        unit: suggested.unit || "m",
      });
      setStatus(`Intake OK: ${sku} — confirm dimensions below`);
      setSku("");
      setLabel("");
      setPhotoFile(null);
      mutate();
    } catch (err) {
      setStatus(err instanceof ApiOfflineError ? "API offline" : err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmDims(e) {
    e.preventDefault();
    if (!pendingItem?.id) return;
    setBusy(true);
    setStatus("");
    try {
      await confirmItemDims(pendingItem.id, {
        l: Number(pendingDims.l),
        w: Number(pendingDims.w),
        h: Number(pendingDims.h),
        unit: pendingDims.unit,
      });
      setStatus(`Dimensions confirmed for ${pendingItem.sku}`);
      setPendingItem(null);
      mutate();
      if (selected?.id) {
        const space = await fetchLocationSpace(selected.id);
        setSpaceInfo(space);
      }
    } catch (err) {
      setStatus(err instanceof ApiOfflineError ? "API offline" : err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleScanMove(e) {
    e.preventDefault();
    setBusy(true);
    setStatus("");
    try {
      const res = await scanMove({ item_qr_or_id: itemRef, to_location_qr: destQr });
      setStatus(`Moved to ${res.to_location_code}`);
      setItemRef("");
      setDestQr("");
      mutate();
    } catch (err) {
      setStatus(err instanceof ApiOfflineError ? "API offline" : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell title="WAREHOUSE">
      <div className="cx-warehouse-grid">
        <section className="cx-panel">
          <p className="cx-label">LOCATION TREE</p>
          {isLoading ? <p className="cx-muted">Loading…</p> : null}
          {!tree.length && !isLoading ? (
            <p className="cx-muted cx-mono">No locations — seed via API or FDE setup.</p>
          ) : null}
          {tree.map((n) => (
            <TreeNode
              key={n.id}
              node={n}
              selected={selected?.id}
              onSelect={setSelected}
            />
          ))}
        </section>

        <section className="cx-panel">
          <p className="cx-label">BIN DETAIL</p>
          {selected ? (
            <>
              <div className="cx-mono cx-warehouse-meta">
                {selected.code} · {selected.qr_token}
              </div>
              <a
                href={warehouseQrLabelUrl(selected.id)}
                target="_blank"
                rel="noreferrer"
                className="cx-btn-secondary"
              >
                PRINT QR LABEL (PNG)
              </a>
              <ul className="cx-warehouse-items">
                {selectedItems.map((it) => (
                  <li key={it.id} className="cx-mono">
                    {it.sku} — {it.label}
                    {it.dims
                      ? ` · ${it.dims.l}×${it.dims.w}×${it.dims.h}${it.dims.unit || "m"}`
                      : ""}
                  </li>
                ))}
              </ul>
              {spaceInfo ? (
                <div className="cx-mono cx-warehouse-meta" style={{ marginTop: "12px" }}>
                  CAP {spaceInfo.capacity_volume ?? "—"} · OCC {spaceInfo.occupied_volume} · FREE{" "}
                  {spaceInfo.free_volume ?? "—"} {spaceInfo.unit}
                </div>
              ) : null}
            </>
          ) : (
            <p className="cx-muted">Select a location.</p>
          )}
        </section>

        <section className="cx-panel">
          <p className="cx-label">INTAKE (PHOTO)</p>
          <form onSubmit={handleIntake} className="cx-warehouse-form">
            <input
              className="cx-input cx-mono"
              placeholder="SKU"
              value={sku}
              onChange={(e) => setSku(e.target.value)}
              required
            />
            <input
              className="cx-input cx-mono"
              placeholder="Label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              required
            />
            <input
              className="cx-input cx-mono"
              placeholder="Location code"
              value={locationCode}
              onChange={(e) => setLocationCode(e.target.value)}
              required
            />
            <input
              type="file"
              accept="image/*"
              capture="environment"
              onChange={(e) => setPhotoFile(e.target.files?.[0] || null)}
            />
            <button type="submit" className="cx-btn-primary" disabled={busy}>
              RECORD INTAKE
            </button>
          </form>

          {pendingItem ? (
            <>
              <p className="cx-label" style={{ marginTop: "24px" }}>
                CONFIRM DIMENSIONS — {pendingItem.sku}
              </p>
              <form onSubmit={handleConfirmDims} className="cx-warehouse-form">
                <input
                  className="cx-input cx-mono"
                  placeholder="Length"
                  value={pendingDims.l}
                  onChange={(e) => setPendingDims((d) => ({ ...d, l: e.target.value }))}
                  required
                />
                <input
                  className="cx-input cx-mono"
                  placeholder="Width"
                  value={pendingDims.w}
                  onChange={(e) => setPendingDims((d) => ({ ...d, w: e.target.value }))}
                  required
                />
                <input
                  className="cx-input cx-mono"
                  placeholder="Height"
                  value={pendingDims.h}
                  onChange={(e) => setPendingDims((d) => ({ ...d, h: e.target.value }))}
                  required
                />
                <button type="submit" className="cx-btn-secondary" disabled={busy}>
                  CONFIRM AS FACT
                </button>
              </form>
            </>
          ) : null}

          <p className="cx-label" style={{ marginTop: "24px" }}>
            SCAN TO MOVE
          </p>
          <form onSubmit={handleScanMove} className="cx-warehouse-form">
            <input
              className="cx-input cx-mono"
              placeholder="Item SKU or ID"
              value={itemRef}
              onChange={(e) => setItemRef(e.target.value)}
              required
            />
            <input
              className="cx-input cx-mono"
              placeholder="Destination QR token"
              value={destQr}
              onChange={(e) => setDestQr(e.target.value)}
              required
            />
            <button type="submit" className="cx-btn-secondary" disabled={busy}>
              RECORD MOVE
            </button>
          </form>
          {status ? <p className="cx-mono cx-warehouse-status">{status}</p> : null}
        </section>
      </div>
    </AppShell>
  );
}
