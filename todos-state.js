// Pure state mutation functions for todos.html.
// Each function returns a new state object — never mutates in place.

function toggleDone(state, section, id) {
  return {
    ...state,
    todos: {
      ...state.todos,
      [section]: (state.todos[section] || []).map(item =>
        item.id === id ? { ...item, done: !item.done } : item
      ),
    },
  };
}

function clearDone(state) {
  const sections = ['today', 'thisWeek', 'someday']; // Fixed by design; update here if sections ever change
  const cleaned = {};
  for (const s of sections) cleaned[s] = (state.todos[s] || []).filter(i => !i.done);
  return { ...state, todos: { ...state.todos, ...cleaned } };
}

function moveItem(state, fromSection, toSection, id) {
  if (fromSection === toSection) return state;
  const item = (state.todos[fromSection] || []).find(i => i.id === id);
  if (!item) return state;
  return {
    ...state,
    todos: {
      ...state.todos,
      [fromSection]: state.todos[fromSection].filter(i => i.id !== id),
      [toSection]:   [...(state.todos[toSection] || []), item],
    },
  };
}

function promoteToKanban(state, section, id) {
  const item = (state.todos[section] || []).find(i => i.id === id);
  if (!item) return state;
  return {
    ...state,
    todo: [
      ...(state.todo || []),
      { id: crypto.randomUUID(), title: item.title, body: '', tag: '', tagClass: '' },
    ],
    todos: {
      ...state.todos,
      [section]: state.todos[section].filter(i => i.id !== id),
    },
  };
}
