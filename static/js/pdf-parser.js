/* ===== pdf-parser.js — PDF 페이지 렌더러 (전체/썸네일/영역 크롭) =====
 * KRA 출주표는 한글이 벡터 패스라 getTextContent 불가 → canvas 렌더 → PNG(base64) → 서버 Vision.
 * - renderThumb: 페이지 분류(detect)용 저해상도 썸네일
 * - renderBand:  경주 요약 페이지의 "메인 출전마 표" 밴드만 고해상도 크롭(추출 정확도↑)
 * - renderPage:  전체 페이지(기수현황표 등)
 * PDF.js는 CDN 동적 import.
 */
(function (global) {
  const PDFJS_VER = '4.0.379';
  // 경주 요약 페이지의 메인 출전마 표 밴드(높이 10%~41%). 전체 폭.
  const BAND = { x0: 0, y0: 0.10, x1: 1.0, y1: 0.41 };
  // 조교훈련/레이팅 표 밴드(높이 35.5%~58.5%). 레이팅·조교사·평가기호.
  const TRAIN_BAND = { x0: 0, y0: 0.355, x1: 1.0, y1: 0.585 };
  let _pdfjsLib = null, _doc = null, _numPages = 0;

  async function _getPdfjs() {
    if (_pdfjsLib) return _pdfjsLib;
    _pdfjsLib = await import(`https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${PDFJS_VER}/pdf.min.mjs`);
    _pdfjsLib.GlobalWorkerOptions.workerSrc =
      `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${PDFJS_VER}/pdf.worker.min.mjs`;
    return _pdfjsLib;
  }

  async function load(file) {
    const pdfjs = await _getPdfjs();
    const buf = await file.arrayBuffer();
    _doc = await pdfjs.getDocument({ data: buf }).promise;
    _numPages = _doc.numPages;
    return _numPages;
  }
  function numPages() { return _numPages; }

  /** 페이지의 frac 영역을 targetW 픽셀 폭으로 렌더 → image content block */
  async function _renderRegion(pageNum, frac, targetW) {
    if (!_doc) throw new Error('PDF가 로드되지 않았습니다.');
    const page = await _doc.getPage(pageNum);
    const base = page.getViewport({ scale: 1 });
    const regionWpt = (frac.x1 - frac.x0) * base.width;
    const scale = Math.max(0.3, Math.min(6, targetW / regionWpt));
    const vp = page.getViewport({ scale });

    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round((frac.x1 - frac.x0) * vp.width));
    canvas.height = Math.max(1, Math.round((frac.y1 - frac.y0) * vp.height));
    const ctx = canvas.getContext('2d');
    // 영역 좌상단이 (0,0)에 오도록 평행이동
    const transform = [1, 0, 0, 1, -frac.x0 * vp.width, -frac.y0 * vp.height];
    await page.render({ canvasContext: ctx, viewport: vp, transform }).promise;

    const data = canvas.toDataURL('image/png').split(',')[1];
    canvas.width = canvas.height = 0;
    if (page.cleanup) page.cleanup();
    return { pageNum, block: { type: 'image', source: { type: 'base64', media_type: 'image/png', data } } };
  }

  function renderThumb(pageNum, w = 640) { return _renderRegion(pageNum, { x0: 0, y0: 0, x1: 1, y1: 1 }, w); }
  function renderBand(pageNum) { return _renderRegion(pageNum, BAND, 1568); }
  function renderTrainingBand(pageNum) { return _renderRegion(pageNum, TRAIN_BAND, 1568); }
  function renderPage(pageNum) { return _renderRegion(pageNum, { x0: 0, y0: 0, x1: 1, y1: 1 }, 1540); }

  global.PdfParser = { load, numPages, renderThumb, renderBand, renderTrainingBand, renderPage };
})(window);
