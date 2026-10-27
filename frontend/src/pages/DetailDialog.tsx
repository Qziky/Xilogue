import { useEffect, useState } from "react";
import { Accordion, AccordionHeader, AccordionItem, AccordionPanel, Badge, Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Image, MessageBar, MessageBarBody, Spinner } from "@fluentui/react-components";
import { Delete20Regular } from "@fluentui/react-icons";
import { when } from "../api";
import { useUi } from "../context";
import type { Document } from "../types";
import { useImage } from "../useImage";

const statuses: Record<string,string> = {pending:"等待识别", running:"正在识别", done:"已完成", failed:"识别失败"};

export function DetailDialog({id, onClose}: {id: string | null; onClose: (deleted?: boolean) => void}) {
  const {api, status, notify, refresh, openRecord, invalidateRecords} = useUi();
  const [document, setDocument] = useState<Document | null>(null);
  const [working, setWorking] = useState(false), [confirm, setConfirm] = useState(false);
  const image = useImage(api, id);
  useEffect(() => {
    let active = true;
    if (!id) return;
    setDocument(null); setConfirm(false);
    void api.request<Document>(`/api/observations/${encodeURIComponent(id)}`)
      .then((value) => {if (active) setDocument(value);})
      .catch((error: Error) => {if (active) {notify(error.message, true); onClose();}});
    return () => { active = false; };
  }, [api, id, notify, onClose]);
  const editable = status?.state === "stopped" || status?.state === "paused";
  async function act(action: "delete" | "retry-ocr") {
    if (!id || working) return;
    setWorking(true);
    try {
      const result = await api.request<{status: string}>(`/api/observations/${encodeURIComponent(id)}/actions`, {body: {action}});
      if (action === "delete") {
        notify(result.status === "cleanup_pending" ? "记录已隐藏，原图文件尚待清理。" : "记录及其文字、索引和任务已删除。");
        invalidateRecords(); onClose(true);
      } else {
        setDocument((previous) => previous && {...previous, ocr_status: "pending", ocr_attempts: 0});
        notify("这条任务已重置，可单独识别已保存图片。");
        invalidateRecords();
      }
      await refresh();
    } catch (error) {notify((error as Error).message, true);}
    finally {setWorking(false);}
  }
  return <Dialog open={!!id} onOpenChange={(_, data) => {if (!data.open) onClose();}}>
    <DialogSurface className="detail-dialog">
      <DialogBody><DialogTitle>{document?.window_title || "观察详情"}</DialogTitle>
        <DialogContent>{!document ? <Spinner label="正在读取来源" /> : <>
          <div className="caption">{document.app_name} · {when(document.captured_at_utc)} · <Badge appearance="tint">{statuses[document.ocr_status || ""] || "尚无识别任务"}</Badge></div>
          {document.browser && <p className="caption">{document.browser.url} · 主动保存的可见页面{document.browser.truncated ? " · 文字已截断" : ""}</p>}
          {document.browser?.nearest_desktop_id && <Button appearance="subtle" onClick={() => openRecord(document.browser!.nearest_desktop_id!)}>查看相近桌面记录（关联需核对）</Button>}
          <div className="dialog-body"><div className="preview">{image.url ? <Image src={image.url} alt="所选记录原图" fit="contain" /> : image.error ? <p className="empty-state">图片不可用，可能已删除或文件缺失。</p> : <Spinner label="正在读取图片" />}</div>
            <div><strong>识别文字</strong><p className="caption">{statuses[document.ocr_status || ""] || "尚无识别任务"} · 已尝试 {document.ocr_attempts} 次</p>
              <pre className="dialog-text">{document.ocr_ready ? document.text.slice(0, 32000) || "未识别到可见文字。" : document.ocr_status === "failed" ? "识别失败，可先暂停再单独重试。" : "OCR 尚未完成，可在概览单独识别已保存图片。"}</pre>
              <Accordion collapsible><AccordionItem value="regions"><AccordionHeader>文字区域与置信度</AccordionHeader><AccordionPanel><pre className="dialog-text">{document.lines.slice(0,100).map((line, i) => `${i+1}. ${line.text}\n置信度 ${(line.confidence*100).toFixed(1)}% · ${JSON.stringify(line.box)}`).join("\n\n")}</pre></AccordionPanel></AccordionItem></Accordion>
            </div></div>
          {confirm && <MessageBar intent="warning"><MessageBarBody>确认删除这条记录？原图、文字及派生内容会被清理，此操作无法撤销。</MessageBarBody></MessageBar>}
        </>}</DialogContent>
        <DialogActions>
          {document?.ocr_status === "failed" && <Button disabled={!editable || working} onClick={() => void act("retry-ocr")}>重试这条 OCR</Button>}
          {confirm ? <><Button disabled={working} onClick={() => setConfirm(false)}>保留记录</Button><Button appearance="primary" disabled={working} onClick={() => void act("delete")}>确认删除</Button></> : <Button icon={<Delete20Regular />} disabled={!document || working} onClick={() => setConfirm(true)}>删除这条记录</Button>}
          <Button appearance="secondary" onClick={() => onClose()}>关闭</Button>
        </DialogActions>
      </DialogBody>
    </DialogSurface>
  </Dialog>;
}
