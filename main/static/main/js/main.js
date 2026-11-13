const directions = document.querySelectorAll('input[name="direction"]');
const sectionInputs = document.querySelectorAll('input[name="section"]');
const sections = JSON.parse(document.getElementById('section-directions').textContent);
function filterSections(resetSelection = false) {
  const selected = document.querySelector('input[name="direction"]:checked');
  const allowed = new Set(sections.filter(item => selected && String(item.direction_id) === selected.value).map(item => String(item.id)));
  sectionInputs.forEach(input => {
    const available = allowed.has(input.value);
    input.disabled = !available;
    input.closest('label').parentElement.hidden = !available;
    if (resetSelection || !available) input.checked = false;
  });
}
directions.forEach(input => input.addEventListener('change', () => filterSections(true)));
filterSections();
