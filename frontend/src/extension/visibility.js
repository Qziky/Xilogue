// Executed in the active tab after an explicit click. Keep this function self-contained.
export function visiblePage() {
  const blocks = [], started = performance.now(), width = innerWidth, height = innerHeight;
  const painted = element => {for(let current=element;current;current=current.parentElement){const style=getComputedStyle(current);if(style.display==="none" || style.visibility!=="visible" || Number(style.opacity)===0)return false;}return true;};
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node, scanned = 0, chars = 0, truncated = false;
  while ((node = walker.nextNode())) {
    if (++scanned > 20000 || performance.now() - started > 500 || chars > 45000 || blocks.length >= 1000) { truncated = true; break; }
    const parent = node.parentElement;
    if (!parent || parent.closest('script,style,noscript,input,textarea,select,[contenteditable],[hidden],[aria-hidden="true"]') || !node.textContent.trim()) continue;
    if (!painted(parent)) continue;
    const range = document.createRange(); range.selectNodeContents(node);
    if (![...range.getClientRects()].some(r => r.bottom > 0 && r.top < height && r.right > 0 && r.left < width)) continue;
    let text = "", box = [width,height,0,0];
    for (let i=0;i<node.length;i++) {
      if (performance.now() - started > 500 || chars + text.length >= 45000) { truncated=true; break; }
      range.setStart(node,i); range.setEnd(node,i+1); const r=range.getBoundingClientRect();
      if (r.width <= 0 || r.top < 0 || r.bottom > height || r.left < 0 || r.right > width) continue;
      const hit = document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
      if (parent!==hit) continue;
      text += node.textContent[i]; box=[Math.min(box[0],r.left),Math.min(box[1],r.top),Math.max(box[2],r.right),Math.max(box[3],r.bottom)];
      if (text.length >= 8000) { truncated=true; break; }
    }
    if (text.trim()) { blocks.push({text:text.trim(),rect:box}); chars+=text.length; }
  }
  // Only currently active subtitle cues of an on-screen video; never the full track.
  for (const video of document.querySelectorAll("video")) {
    const r=video.getBoundingClientRect(); if(!painted(video) || r.top<0 || r.bottom>height || r.left<0 || r.right>width || document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)!==video) continue;
    for(const track of video.textTracks) if(track.mode==="showing") for(const cue of track.activeCues || []) {
      if(blocks.length<1024 && String(cue.text).length<=8000) blocks.push({text:String(cue.text),rect:[r.left,r.top,r.right,r.bottom]});
    }
  }
  return {url:location.href,title:document.title,blocks,viewport:[width,height],truncated};
}
