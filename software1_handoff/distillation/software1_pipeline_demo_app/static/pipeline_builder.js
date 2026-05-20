/* global Sortable */
(function () {
  'use strict';

  var paletteEl = document.getElementById('palette');
  var pipelineEl = document.getElementById('pipelineSteps');
  var specHidden = document.getElementById('specHidden');
  var specPreview = document.getElementById('specPreview');
  var form = document.getElementById('runForm');
  var presetSelect = document.getElementById('presetPick');
  var btnClear = document.getElementById('btnClearPipeline');
  var catalog = window.SOLVER_CATALOG || [];

  function findCatalog(solverId) {
    for (var i = 0; i < catalog.length; i++) {
      if (catalog[i].id === solverId) return catalog[i];
    }
    return { id: solverId, desc: '', params: [] };
  }

  function escapeHtml(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function buildParamRows(solverId) {
    var c = findCatalog(solverId);
    if (!c.params || !c.params.length) return '';
    var html = '<div class="param-grid">';
    c.params.forEach(function (p) {
      var step = p.step != null ? p.step : '';
      var def = p.default != null ? p.default : '';
      html += '<label class="param-cell">' + escapeHtml(p.label || p.key);
      html += '<input type="' + (p.type || 'text') + '" class="param-field" data-param-key="' +
        escapeHtml(p.key) + '" step="' + step + '" value="' + def + '"/>';
      html += '</label>';
    });
    html += '</div>';
    return html;
  }

  function createStepLi(solverId) {
    var c = findCatalog(solverId);
    var li = document.createElement('li');
    li.className = 'pipeline-step';
    li.setAttribute('data-solver', solverId);
    li.innerHTML =
      '<div class="step-toolbar">' +
        '<span class="step-handle" title="拖动排序">&#9776;</span>' +
        '<span class="step-badge">' + escapeHtml(solverId) + '</span>' +
        '<span class="step-desc">' + escapeHtml(c.desc) + '</span>' +
        '<button type="button" class="btn-remove" title="移除此步">&#10005;</button>' +
      '</div>' +
      '<div class="step-fields">' +
        '<label class="fld">本步输入 CSV 来源' +
        '<select class="input-src">' +
          '<option value="previous">上一步（或第一步=上传文件）</option>' +
          '<option value="initial">始终用上传的原始 CSV</option>' +
          '<option value="step">指定前面的某一跳…</option>' +
        '</select></label>' +
        '<div class="step-ref-fields">' +
          '<label>第几步（从 0 计数）<input type="number" class="step-index" min="0" step="1" value="0"/></label>' +
          '<label>返回里的 CSV 键 <input type="text" class="csv-key" placeholder="auto / filled_csv / flags_csv …" value="auto"/></label>' +
        '</div>' +
        buildParamRows(solverId) +
        '<label class="fld">本步目录名（可选）<input type="text" class="step-name" placeholder="默认 01_xxx"/></label>' +
        '<details class="adv"><summary>高级：列映射 JSON</summary>' +
        '<textarea class="mapping-json" rows="3" placeholder="例如 {&quot;id_col&quot;:&quot;PatientID&quot;}"></textarea></details>' +
        '<details class="adv"><summary>高级：额外 params（与上方数字参数合并）</summary>' +
        '<textarea class="params-json" rows="2">{}</textarea></details>' +
      '</div>';
    wireStepLi(li);
    return li;
  }

  function wireStepLi(li) {
    var src = li.querySelector('.input-src');
    var refBox = li.querySelector('.step-ref-fields');
    function toggleRef() {
      var show = src.value === 'step';
      refBox.style.display = show ? 'flex' : 'none';
    }
    src.addEventListener('change', function () {
      toggleRef();
      syncSpec();
    });
    toggleRef();
    li.querySelector('.btn-remove').addEventListener('click', function () {
      li.remove();
      syncSpec();
    });
    li.querySelectorAll('input, select, textarea').forEach(function (el) {
      if (el.classList.contains('step-handle')) return;
      el.addEventListener('change', syncSpec);
      el.addEventListener('input', syncSpec);
    });
  }

  function collectParams(li) {
    var params = {};
    var pj = li.querySelector('.params-json').value.trim();
    if (pj && pj !== '{}') {
      try {
        var o = JSON.parse(pj);
        if (o && typeof o === 'object') Object.assign(params, o);
      } catch (e) {
        throw new Error('params JSON: ' + e.message);
      }
    }
    li.querySelectorAll('.param-field').forEach(function (inp) {
      var k = inp.getAttribute('data-param-key');
      if (!k || inp.value === '' || inp.value == null) return;
      if (inp.type === 'number') params[k] = parseFloat(inp.value);
      else params[k] = inp.value;
    });
    return Object.keys(params).length ? params : undefined;
  }

  function collectMapping(li) {
    var raw = li.querySelector('.mapping-json').value.trim();
    if (!raw) return undefined;
    try {
      return JSON.parse(raw);
    } catch (e) {
      throw new Error('mapping JSON: ' + e.message);
    }
  }

  function syncSpec() {
    var steps = [];
    var lis = pipelineEl.querySelectorAll('.pipeline-step');
    try {
      lis.forEach(function (li) {
        var solver = li.getAttribute('data-solver');
        var src = li.querySelector('.input-src').value;
        var step = { solver: solver };
        var customName = li.querySelector('.step-name').value.trim();
        if (customName) step.name = customName;
        if (src === 'initial') {
          step.from = 'initial';
        } else if (src === 'step') {
          step.from = 'step';
          step.step_index = parseInt(li.querySelector('.step-index').value, 10) || 0;
          var ck = li.querySelector('.csv-key').value.trim() || 'auto';
          step.csv_key = ck;
        }
        var pmap = collectMapping(li);
        if (pmap && typeof pmap === 'object' && Object.keys(pmap).length > 0) {
          step.mapping = pmap;
        }
        var par = collectParams(li);
        if (par) step.params = par;
        steps.push(step);
      });
    } catch (e) {
      if (specPreview) specPreview.textContent = String(e.message || e);
      if (specHidden) specHidden.value = '';
      return;
    }
    var spec = { steps: steps };
    var text = JSON.stringify(spec, null, 2);
    if (specHidden) specHidden.value = text;
    if (specPreview) specPreview.textContent = text;
  }

  function applyFromSpec(spec) {
    if (!spec || !spec.steps) return;
    pipelineEl.innerHTML = '';
    if (!spec.steps.length) {
      syncSpec();
      return;
    }
    spec.steps.forEach(function (s) {
      var li = createStepLi(s.solver);
      if (s.from === 'initial') li.querySelector('.input-src').value = 'initial';
      else if (s.from === 'step') {
        li.querySelector('.input-src').value = 'step';
        li.querySelector('.step-index').value = String(s.step_index != null ? s.step_index : 0);
        li.querySelector('.csv-key').value = s.csv_key != null ? String(s.csv_key) : 'auto';
      } else {
        li.querySelector('.input-src').value = 'previous';
      }
      if (s.name) li.querySelector('.step-name').value = s.name;
      if (s.mapping) {
        li.querySelector('.mapping-json').value = JSON.stringify(s.mapping, null, 2);
      }
      if (s.params) {
        li.querySelector('.params-json').value = JSON.stringify(s.params, null, 2);
        Object.keys(s.params).forEach(function (k) {
          var inp = li.querySelector('.param-field[data-param-key="' + k + '"]');
          if (inp) inp.value = String(s.params[k]);
        });
      }
      li.querySelector('.input-src').dispatchEvent(new Event('change'));
      pipelineEl.appendChild(li);
    });
    syncSpec();
  }

  if (paletteEl && typeof Sortable !== 'undefined') {
    new Sortable(paletteEl, {
      group: { name: 'pipeline', pull: 'clone', put: false },
      sort: false,
      animation: 150,
    });
    new Sortable(pipelineEl, {
      group: { name: 'pipeline', pull: true, put: true },
      handle: '.step-handle',
      animation: 150,
      onAdd: function (evt) {
        var sid = evt.item.getAttribute('data-solver');
        if (!sid) return;
        var fresh = createStepLi(sid);
        evt.item.parentNode.replaceChild(fresh, evt.item);
        syncSpec();
      },
      onEnd: function () { syncSpec(); },
    });
  } else if (paletteEl) {
    paletteEl.addEventListener('dblclick', function (e) {
      var t = e.target.closest('.palette-item');
      if (!t) return;
      var sid = t.getAttribute('data-solver');
      if (sid) {
        pipelineEl.appendChild(createStepLi(sid));
        syncSpec();
      }
    });
  }

  if (presetSelect && window.PRESETS) {
    presetSelect.addEventListener('change', function () {
      var k = presetSelect.value;
      if (!k || !window.PRESETS[k]) return;
      try {
        applyFromSpec(JSON.parse(window.PRESETS[k]));
      } catch (e) {
        if (specPreview) specPreview.textContent = '预设解析失败：' + e.message;
      }
    });
  }

  if (btnClear) {
    btnClear.addEventListener('click', function () {
      pipelineEl.innerHTML = '';
      syncSpec();
    });
  }

  if (form) {
    form.addEventListener('submit', function (e) {
      syncSpec();
      var raw = specHidden && specHidden.value ? specHidden.value.trim() : '';
      if (!raw) {
        e.preventDefault();
        alert('请先拖入算子，或检查映射 JSON 是否有语法错误。');
        return;
      }
      var spec;
      try {
        spec = JSON.parse(raw);
      } catch (err) {
        e.preventDefault();
        alert('管线 JSON 无效：' + err.message);
        return;
      }
      if (!spec.steps || !spec.steps.length) {
        e.preventDefault();
        alert('管线至少需要一个算子。');
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var pf = document.getElementById('paletteFilter');
    if (pf && paletteEl) {
      pf.addEventListener('input', function () {
        var q = pf.value.trim().toLowerCase();
        paletteEl.querySelectorAll('.palette-item').forEach(function (li) {
          var t = (li.textContent || '').toLowerCase();
          li.style.display = !q || t.indexOf(q) >= 0 ? '' : 'none';
        });
      });
    }
    if (window.PRESETS && window.PRESETS.lab_eda_4step) {
      try {
        applyFromSpec(JSON.parse(window.PRESETS.lab_eda_4step));
      } catch (e) {
        if (specPreview) specPreview.textContent = '默认预设加载失败：' + e.message;
        syncSpec();
      }
    } else {
      syncSpec();
    }
  });
})();
