// Xenosaga II ISO Editor (web) — under active reverse-engineering.
//
// The uncompressed region of disc 1 holds the game's TEXT tables (item / key-item /
// skill / enemy names + descriptions), which the Reference tab surfaces. The numeric
// BALANCE tables (enemy HP/stats, skill power/target, cast times, boost/break rules)
// are NOT stored there as raw values — verified against two independent sources
// (xenoserieswiki + a strategy guide), the guide's enemy HP appears nowhere on the
// disc, so those tables are packed inside the large XENOSAGA.* archives. Locating and
// safely rewriting them is in progress; no unverified ISO writer ships until then.
(function(){
  const $=(s,r=document)=>r.querySelector(s);

  window.initISO = async function(){
    const root=$("#isoRoot"); if(root.dataset.init) return; root.dataset.init="1";
    root.innerHTML =
      '<div class="card">'+
        '<h2>ISO editing — under research</h2>'+
        '<p>The Save editor (left tab) is complete and verified. Direct <b>ISO</b> editing '+
        '(enemy HP rebalancing, skill power / targeting, cast-time & combo tuning) is still '+
        'being reverse-engineered.</p>'+
        '<p class="note">Why the wait: the disc\'s uncompressed area contains the game\'s '+
        '<b>text</b> (names &amp; descriptions — see the Reference tab), but the numeric '+
        '<b>balance</b> tables are packed inside the large <code>XENOSAGA.*</code> archives. '+
        'Cross-checking against the xenoserieswiki and a strategy guide confirmed the '+
        'on-disc enemy stats we first found were not the real battle values, so they were '+
        'pulled rather than ship a writer that edits the wrong bytes. The real tables are '+
        'being located now — this tab returns once edits can be verified.</p>'+
      '</div>';
  };
})();
