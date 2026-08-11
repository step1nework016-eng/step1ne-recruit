// 人格特質測驗題本與計分
//
// 三個量表，三種不同的東西，**計分方式與可信度不一樣，不要混在一起看**：
//
// 1. Big Five（Mini-IPIP 改寫，20 題）— normative，可跨人比較
//    題本來源 IPIP（International Personality Item Pool）為公共領域。
//    中文題目是依構念改寫成職場情境，**不是官方中文版、沒有台灣常模**。
//    → 報告一定要標明「相對描述，不是標準化分數」。
//
// 2. Grit-S（Duckworth & Quinn 2009，8 題）— normative
//    補 Big Five 沒有的「長期不放棄」。同樣是自行改寫，非官方中文版。
//
// 3. DISC（沿用原本自寫的題本，20 組）— **ipsative，不可跨人比較**
//    只當工作風格的描述語言，不進分數、不排序、不當門檻。
//
// ⚠️ 為什麼不用 MBTI／Hogan／16PF：那些是商業授權量表，題目有版權，
//    不能直接抄。MBTI 另外還有重測信度低的問題，學界不採用。

// ── Big Five：Mini-IPIP 20 題 ──
// dim: O/C/E/A/N，rev: true 代表反向計分（1↔5）
const BIG5 = [
  { t: '在團體討論裡，我通常是帶頭發言的人', dim: 'E' },
  { t: '我不太主動跟不熟的人攀談', dim: 'E', rev: true },
  { t: '在聚會或活動場合，我會跟很多不同的人聊天', dim: 'E' },
  { t: '我比較習慣待在旁邊看，不太站到前面', dim: 'E', rev: true },

  { t: '別人心情不好時，我通常感覺得出來', dim: 'A' },
  { t: '別人遇到的麻煩，我不太會放在心上', dim: 'A', rev: true },
  { t: '我能體會別人的處境和感受', dim: 'A' },
  { t: '老實說，我對別人的事情沒什麼興趣', dim: 'A', rev: true },

  { t: '交辦的事情我會盡快處理掉，不喜歡拖', dim: 'C' },
  { t: '東西用完我常忘記歸位', dim: 'C', rev: true },
  { t: '我喜歡把事情安排得有條理', dim: 'C' },
  { t: '我做事常常收尾收得不夠乾淨', dim: 'C', rev: true },

  { t: '我的情緒起伏蠻大的', dim: 'N' },
  { t: '大部分時候我都還算放鬆', dim: 'N', rev: true },
  { t: '我容易因為一些事情就心煩', dim: 'N' },
  { t: '我很少覺得低落', dim: 'N', rev: true },

  { t: '我對新的做法和新的東西很有興趣', dim: 'O' },
  { t: '我對比較抽象、理論性的東西沒興趣', dim: 'O', rev: true },
  { t: '要我理解比較抽象的概念有點吃力', dim: 'O', rev: true },
  { t: '我常想到一些別人沒想到的做法', dim: 'O' },
];

// ── Grit-S 8 題 ──
// sub: 'interest' 興趣持續性（全部反向）／'effort' 努力持續性
const GRIT = [
  { t: '新的想法或新專案出現時，我常常就把原本在做的事放掉了', sub: 'interest', rev: true },
  { t: '我曾經對某件事很投入，但過一陣子就失去興趣', sub: 'interest', rev: true },
  { t: '我常常訂了一個目標，後來又改成追別的目標', sub: 'interest', rev: true },
  { t: '要花好幾個月才能完成的事，我很難一直保持專注', sub: 'interest', rev: true },
  { t: '遇到挫折不太會讓我打退堂鼓', sub: 'effort' },
  { t: '我是一個肯下功夫的人', sub: 'effort' },
  { t: '只要是我開始做的事，我都會做完', sub: 'effort' },
  { t: '我做事很勤奮', sub: 'effort' },
];

const LIKERT = ['非常不同意', '不太同意', '普通', '還算同意', '非常同意'];

// ── 計分 ──
// answers: { b5: [1..5 ×20], grit: [1..5 ×8], disc: [{most, least} ×20] }
function score(answers, discBank) {
  const val = (v, rev) => (rev ? 6 - v : v);

  // Big Five：每維 4 題取平均（1–5）
  const b5sum = { O: [], C: [], E: [], A: [], N: [] };
  BIG5.forEach((item, i) => {
    const a = answers.b5?.[i];
    if (a >= 1 && a <= 5) b5sum[item.dim].push(val(a, item.rev));
  });
  const avg = (arr) => (arr.length ? arr.reduce((s, x) => s + x, 0) / arr.length : null);
  const b5 = {
    O: avg(b5sum.O), C: avg(b5sum.C), E: avg(b5sum.E),
    A: avg(b5sum.A), N: avg(b5sum.N),
  };

  // Grit：8 題平均，另外拆兩個子維度
  const gi = [], ge = [];
  GRIT.forEach((item, i) => {
    const a = answers.grit?.[i];
    if (a >= 1 && a <= 5) (item.sub === 'interest' ? gi : ge).push(val(a, item.rev));
  });
  const grit = { total: avg([...gi, ...ge]), interest: avg(gi), effort: avg(ge) };

  // DISC：只算「最像」被選中的次數（沿用原本邏輯）
  const disc = { D: 0, I: 0, S: 0, C: 0 };
  (answers.disc || []).forEach((pick, gi2) => {
    if (!pick || typeof pick.most !== 'number') return;
    const group = discBank?.[gi2];
    const dim = group?.[pick.most]?.L;
    if (dim && disc[dim] !== undefined) disc[dim] += 1;
  });
  const primary = Object.entries(disc).sort((a, b) => b[1] - a[1])[0]?.[0] || null;

  // ── 作答品質 ──
  // ⚠️ 這不是「測謊」。人格測驗測不出說謊，這只是在標記
  // 「這份作答資料本身可不可靠」——全部選同一格、或快到不可能讀完題目，
  // 那份分數就不該拿來當判斷依據。
  const flat = [...(answers.b5 || []), ...(answers.grit || [])];
  const allSame = flat.length >= 20 && new Set(flat).size === 1;
  const lowVariance = flat.length >= 20 && new Set(flat).size <= 2;
  let quality = null;
  if (allSame) quality = 'straight_line';
  else if (lowVariance) quality = 'low_variance';
  if (answers.seconds && answers.seconds < 60) {
    quality = quality ? quality + ',too_fast' : 'too_fast';
  }

  return { b5, grit, disc, primary, quality };
}

if (typeof module !== 'undefined') module.exports = { BIG5, GRIT, LIKERT, score };
