"""Deleting a server updates discovery, selections and monitoring together."""
import copy
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

from app.services.agent_registry import AgentRegistry
from app.services.remote_monitor import remote_monitor
from app.services.settings import AppSettings
from app.ui.agent_view import AgentSelectionDialog, AgentView


URL = "mqtt://broker.example.com:8000/deletion-tests"
TOKEN = "test-controller-token-1234567890"
AGENTS = [
    {"agent_id": "a", "name": "Hong Kong", "online": True,
     "last_event": {"state": "running", "total": 12, "success": 12}},
    {"agent_id": "b", "name": "Tokyo", "online": False},
    {"agent_id": "c", "name": "London", "online": True},
]


class ServerDeletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        profile = tempfile.TemporaryDirectory(prefix="flowbench-deletion-")
        self.addCleanup(profile.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": profile.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.config = AppSettings()
        for key, value in (("language", "en-US"), ("agent_hub_url", URL),
                           ("agent_hub_token", TOKEN)):
            self.config.set(key, value)
        for target in ("app.ui.agent_view.settings", "app.ui.i18n.settings"):
            item = patch(target, self.config)
            item.start()
            self.addCleanup(item.stop)
        success = patch("app.ui.agent_view.InfoBar.success")
        success.start()
        self.addCleanup(success.stop)
        remote_monitor.reset()
        self.addCleanup(remote_monitor.reset)
        self.view = self.make_view()
        self.view._on_result("list", copy.deepcopy(AGENTS), None)

    def make_view(self):
        view = AgentView()
        view._poll_timer.stop()
        self.addCleanup(view.deleteLater)
        self.addCleanup(lambda: remote_monitor.refresh_requested.disconnect(view._poll_remote_status))
        view._show_error = Mock()
        view.ask_delete = Mock(return_value=True)
        return view

    def ids(self):
        return [agent["agent_id"] for agent in self.view._agents]

    def test_cancel_keeps_servers_selection_and_monitor(self):
        self.view.ask_delete.return_value = False
        before = self.view._selected_agents_ids()
        with patch.object(self.view, "_client") as client:
            self.view.delete_agents()
        client.assert_not_called()
        self.assertEqual(self.ids(), ["a", "b", "c"])
        self.assertEqual(self.view._selected_agents_ids(), before)
        self.assertEqual(len(remote_monitor.agents()), 3)
        self.assertEqual(self.view._registry.removed(URL, TOKEN), [])

    def test_delete_selected_cleans_lists_without_selecting_another_server(self):
        with patch.object(self.view, "_client") as client:
            self.view.delete_agents()
        client.assert_not_called()  # A local deletion never stops/uninstalls a remote Agent.
        self.assertEqual(self.ids(), ["b", "c"])
        self.assertEqual(self.view.agentCombo.count(), 2)
        self.assertEqual(self.view._selected_agents_ids(), [])
        self.assertTrue(self.view._registry.removed(URL, TOKEN))
        self.assertIsNone(remote_monitor.agent("a"))
        self.assertEqual(self.view.mTotal.text(), "0")
        self.assertNotIn("Hong Kong", self.view.nodeListLabel.text())
        with patch.object(self.view, "_client") as client:
            self.view.run_job()
        client.assert_not_called()

    def test_batch_delete_includes_offline_server_and_cleans_monitor_history(self):
        self.view._multi_selection_active = True
        self.view._selected_agent_ids = {"a", "b"}
        self.view.delete_agents()
        self.assertEqual(self.ids(), ["c"])
        self.assertEqual(self.view._selected_agent_ids, set())
        self.assertEqual([item["agent_id"] for item in remote_monitor.agents()], ["c"])
        self.assertEqual(len(self.view._registry.removed(URL, TOKEN)), 2)

    def test_removed_server_stays_out_of_manual_and_inflight_poll_results(self):
        scope = self.view._connection_scope()
        self.view.delete_agents()
        for action in ("list", f"poll_status:{scope}", f"list:{scope}"):
            self.view._on_result(action, copy.deepcopy(AGENTS), None)
            self.assertEqual(self.ids(), ["b", "c"])
            self.assertEqual(self.view.agentCombo.count(), 2)
            self.assertIsNone(remote_monitor.agent("a"))
        fresh = self.make_view()
        fresh._on_result("list", copy.deepcopy(AGENTS), None)
        self.assertEqual([agent["agent_id"] for agent in fresh._agents], ["b", "c"])

    def test_failed_delete_keeps_selection_and_visible_servers(self):
        with patch.object(self.config, "_atomic_write_json", side_effect=OSError("disk full")):
            self.view.delete_agents()
        self.assertEqual(self.ids(), ["a", "b", "c"])
        self.assertEqual(self.view._selected_agents_ids(), ["a"])
        self.assertEqual(self.view._registry.removed(URL, TOKEN), [])
        self.assertIn("disk full", self.view._show_error.call_args.args[0])

    def test_restore_only_chosen_server_and_refresh_discovery(self):
        self.view._multi_selection_active = True
        self.view._selected_agent_ids = {"a", "b"}
        self.view.delete_agents()
        outer = self.make_selection_dialog(agents=self.view._agents)
        dialog = Mock()
        dialog.exec.return_value = True
        dialog.selected_ids.return_value = ["b"]
        def immediate(action, fn):
            self.view._on_result(action, fn(), None)
        with patch("app.ui.agent_view.AgentSelectionDialog", return_value=dialog), \
                patch.object(self.view, "_client", return_value=Mock(list_agents=lambda: copy.deepcopy(AGENTS))), \
                patch.object(self.view, "_async", side_effect=immediate):
            outer.restoreAgentsBtn.click()
        self.assertEqual(self.ids(), ["b", "c"])
        self.assertEqual(self.view._registry.removed(URL, TOKEN), [{"agent_id": "a", "name": "Hong Kong"}])
        self.assertTrue(outer.restoreAgentsBtn.isEnabled())
        self.assertIn("b", outer.visible_ids())
        self.assertNotIn("b", outer.selected_ids())

    def test_cancel_restore_or_failed_save_leaves_server_removed(self):
        self.view.delete_agents()
        dialog = Mock()
        dialog.exec.return_value = False
        with patch("app.ui.agent_view.AgentSelectionDialog", return_value=dialog):
            self.view.restore_agents()
        dialog.selected_ids.assert_not_called()
        dialog.exec.return_value = True
        dialog.selected_ids.return_value = ["a"]
        with patch("app.ui.agent_view.AgentSelectionDialog", return_value=dialog), \
                patch.object(self.config, "_atomic_write_json", side_effect=OSError("denied")), \
                patch.object(self.view, "refresh_agents") as refresh:
            self.view.restore_agents()
        refresh.assert_not_called()
        self.assertEqual(self.ids(), ["b", "c"])
        self.assertIn("denied", self.view._show_error.call_args.args[0])

    def test_switch_connection_ignores_old_results_and_isolates_removal(self):
        self.view.delete_agents()
        previous = self.view._connection_scope()
        self.view.hubEdit.setText(URL + "-other")
        self.assertEqual(self.ids(), [])
        self.assertFalse(self.view._registry.removed(URL + "-other", TOKEN))
        self.view._on_result(f"poll_status:{previous}", AGENTS, None)
        self.view._on_result(f"list:{previous}", None, RuntimeError("old connection"))
        self.assertEqual(self.ids(), [])
        self.view._show_error.assert_not_called()
        self.view._on_result("list", AGENTS, None)
        self.assertEqual(self.ids(), ["a", "b", "c"])
        self.view.hubEdit.setText(URL)
        self.view._on_result("list", AGENTS, None)
        self.assertEqual(self.ids(), ["b", "c"])

    def test_poll_preserves_selected_id_when_list_order_changes(self):
        self.view.agentCombo.setCurrentIndex(2)
        self.view._on_result("poll_status", list(reversed(AGENTS)), None)
        self.assertEqual(self.view._selected_agents_ids(), ["c"])

    def test_connection_changed_during_confirmation_does_not_delete(self):
        def switch(_, **kwargs):
            self.view.hubEdit.setText(URL + "-other")
            return True
        self.view.ask_delete.side_effect = switch
        self.view.delete_agents()
        registry = AgentRegistry(self.config)
        self.assertEqual(registry.removed(URL, TOKEN), [])
        self.assertEqual(registry.removed(URL + "-other", TOKEN), [])

    def test_restore_dialog_describes_restoring_without_inventing_live_status(self):
        dialog = AgentSelectionDialog([{"agent_id": "a", "name": "Hong Kong"}],
                                      parent=self.view, restoring=True)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.yesButton.text(), "Restore selected")
        self.assertIn("restore", dialog.headingLabel.text())
        self.assertNotIn("task", dialog.descriptionLabel.text())
        row = dialog._rows["a"]
        self.assertEqual((row.status, row.details), ("deleted", "a"))
        self.assertNotIn("older agent", row.toolTip())
        dialog.selectAllBtn.click()
        self.assertEqual(dialog.selected_ids(), ["a"])

    def make_selection_dialog(self, agents=AGENTS):
        dialog = AgentSelectionDialog(agents, ["b"] if agents == AGENTS else [], parent=self.view,
                                      delete_agent=lambda agent_id: self.view.delete_agents([agent_id]),
                                      delete_agents=lambda agent_ids: self.view.delete_agents(agent_ids),
                                      restore_agents=lambda: self.view.restore_agents(parent=dialog),
                                      removed_count=len(self.view._registry.removed(URL, TOKEN)))
        self.addCleanup(dialog.deleteLater)
        self.view._selection_dialog = dialog
        self.addCleanup(lambda: setattr(self.view, "_selection_dialog", None))
        return dialog

    def test_red_row_button_deletes_only_its_server_without_applying_selection(self):
        dialog = self.make_selection_dialog()
        self.view.resize(840, 810)
        self.view.show()
        dialog.show()
        QTest.qWait(80)
        accepted = []
        dialog.accepted.connect(lambda: accepted.append(True))
        row = dialog._rows["a"]
        button = row.deleteBtn
        self.assertIsNotNone(button)
        self.assertGreater(button.geometry().left(), row.width() // 2)
        self.assertFalse(row.check.isChecked())
        for theme in (Theme.LIGHT, Theme.DARK):
            setTheme(theme)
            self.app.processEvents()
            color = button.palette().color(QPalette.ColorRole.ButtonText)
            self.assertGreater(color.red(), color.green())
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.ids(), ["b", "c"])
        self.assertEqual(dialog.visible_ids(), ["b", "c"])
        self.assertEqual(dialog.selected_ids(), ["b"])
        self.assertEqual(accepted, [])
        self.assertEqual(len(self.view.ask_delete.call_args.args[0]), 1)

    def test_cancel_row_delete_leaves_checkbox_and_search_selection_unchanged(self):
        self.view.ask_delete.return_value = False
        dialog = self.make_selection_dialog()
        dialog.searchEdit.setText("Hong")
        row = dialog._rows["a"]
        row.deleteBtn.click()
        self.assertFalse(row.check.isChecked())
        self.assertEqual(dialog.visible_ids(), ["a"])
        self.assertEqual(dialog.selected_ids(), ["b"])
        self.assertEqual(self.ids(), ["a", "b", "c"])

    def test_deleting_last_search_result_updates_empty_state_and_select_all(self):
        dialog = self.make_selection_dialog()
        dialog.searchEdit.setText("Hong")
        dialog._rows["a"].deleteBtn.click()
        self.assertEqual(dialog.visible_ids(), [])
        self.assertFalse(dialog.noMatchLabel.isHidden())
        dialog.searchEdit.clear()
        self.assertEqual(dialog.visible_ids(), ["b", "c"])
        dialog.selectAllBtn.click()
        self.assertEqual(dialog.selected_ids(), ["b", "c"])

    def test_restore_button_is_after_clear_and_works_after_deleting_all_rows(self):
        dialog = self.make_selection_dialog()
        self.view.resize(840, 810)
        self.view.show()
        dialog.show()
        QTest.qWait(50)
        self.assertGreater(dialog.restoreAgentsBtn.x(), dialog.clearBtn.x())
        self.assertFalse(dialog.restoreAgentsBtn.isEnabled())
        for agent_id in ("a", "b", "c"):
            dialog._rows[agent_id].deleteBtn.click()
        self.assertEqual(dialog.visible_ids(), [])
        self.assertTrue(dialog.restoreAgentsBtn.isEnabled())
        self.assertEqual(dialog.selected_ids(), [])
        restored = Mock()
        restored.exec.return_value = True
        restored.selected_ids.return_value = ["a"]
        def immediate(action, fn):
            self.view._on_result(action, fn(), None)
        with patch("app.ui.agent_view.AgentSelectionDialog", return_value=restored), \
                patch.object(self.view, "_client", return_value=Mock(list_agents=lambda: AGENTS)), \
                patch.object(self.view, "_async", side_effect=immediate):
            dialog.restoreAgentsBtn.click()
        self.assertEqual(dialog.visible_ids(), ["a"])
        self.assertEqual(dialog.selected_ids(), [])

    def test_all_deleted_still_allows_opening_server_dialog(self):
        self.view.delete_agents(["a", "b", "c"])
        dialog = Mock()
        dialog.exec.return_value = False
        with patch("app.ui.agent_view.AgentSelectionDialog", return_value=dialog) as factory:
            self.view.select_agents()
        self.view._show_error.assert_not_called()
        self.assertEqual(factory.call_args.kwargs["removed_count"], 3)
        self.assertIsNotNone(factory.call_args.kwargs["restore_agents"])

    def test_toolbar_delete_removes_every_checked_server_in_one_confirmation(self):
        dialog = self.make_selection_dialog()
        self.view.resize(840, 810)
        self.view.show()
        dialog.show()
        QTest.qWait(50)
        self.assertGreater(dialog.deleteAgentsBtn.x(), dialog.clearBtn.x())
        self.assertLess(dialog.deleteAgentsBtn.x(), dialog.restoreAgentsBtn.x())
        dialog._checks["a"].setChecked(True)
        dialog.searchEdit.setText("Hong")  # Tokyo stays checked outside these search results.
        self.assertEqual(dialog.visible_ids(), ["a"])
        self.assertTrue(dialog.deleteAgentsBtn.isEnabled())
        dialog.deleteAgentsBtn.click()
        self.view.ask_delete.assert_called_once()
        confirmed = self.view.ask_delete.call_args.args[0]
        self.assertEqual([agent["agent_id"] for agent in confirmed], ["a", "b"])
        self.assertEqual(self.ids(), ["c"])
        self.assertEqual(dialog.selected_ids(), [])
        self.assertFalse(dialog.deleteAgentsBtn.isEnabled())
        self.assertTrue(dialog.restoreAgentsBtn.isEnabled())
        dialog.searchEdit.clear()
        self.assertEqual(dialog.visible_ids(), ["c"])

    def test_toolbar_delete_all_then_none_updates_enabled_state(self):
        dialog = self.make_selection_dialog()
        dialog.clearBtn.click()
        self.assertFalse(dialog.deleteAgentsBtn.isEnabled())
        dialog.deleteAgentsBtn.click()
        self.view.ask_delete.assert_not_called()
        dialog.selectAllBtn.click()
        self.assertTrue(dialog.deleteAgentsBtn.isEnabled())
        dialog.deleteAgentsBtn.click()
        self.assertEqual(self.ids(), [])
        self.assertEqual(dialog.visible_ids(), [])
        self.assertFalse(dialog.deleteAgentsBtn.isEnabled())
        self.assertTrue(dialog.restoreAgentsBtn.isEnabled())

    def test_toolbar_delete_cancel_and_save_failure_preserve_all_checks(self):
        dialog = self.make_selection_dialog()
        dialog.selectAllBtn.click()
        self.view.ask_delete.return_value = False
        dialog.deleteAgentsBtn.click()
        self.assertEqual(dialog.selected_ids(), ["a", "b", "c"])
        self.assertEqual(self.ids(), ["a", "b", "c"])
        self.view.ask_delete.return_value = True
        with patch.object(self.config, "_atomic_write_json", side_effect=OSError("disk full")):
            dialog.deleteAgentsBtn.click()
        self.assertEqual(dialog.selected_ids(), ["a", "b", "c"])
        self.assertEqual(self.ids(), ["a", "b", "c"])
        self.assertEqual(self.view._registry.removed(URL, TOKEN), [])

    def test_open_dialog_refreshes_existing_rows_and_keeps_pending_checks(self):
        dialog = self.make_selection_dialog()
        row = dialog._rows["b"]
        check = row.check
        button = row.deleteBtn
        fresh = copy.deepcopy(AGENTS)
        fresh[1].update(name="New Tokyo", online=True, clock_skew=180,
                        metadata={"platform": "Linux-6.1", "hostname": "new-host",
                                  "limits": {"max_rate": 250, "max_duration": 600,
                                             "max_threads": 16, "max_packet_size": 1024}})
        self.view._on_result("poll_status", fresh, None)
        self.assertIs(dialog._rows["b"], row)
        self.assertIs(row.check, check)
        self.assertIs(row.deleteBtn, button)
        self.assertTrue(row.online)
        self.assertEqual(row.title, "New Tokyo")
        self.assertEqual(row.os_name, "linux")
        self.assertIn("new-host", row.details)
        self.assertIn("250 QPS", row.toolTip())
        self.assertIn("3 min ahead", row.toolTip())
        self.assertIn("New Tokyo", button.accessibleName())
        self.assertEqual(dialog.selected_ids(), ["b"])
        dialog.searchEdit.setText("New Tokyo online Linux")
        self.assertEqual(dialog.visible_ids(), ["b"])
        dialog._rows["b"].deleteBtn.click()
        self.assertEqual([agent["agent_id"] for agent in self.view.ask_delete.call_args.args[0]], ["b"])
        self.assertNotIn("b", self.ids())

    def test_live_status_changes_reapply_search_in_both_languages_and_themes(self):
        for language, online, offline in (("en-US", "online", "offline"),
                                           ("zh-CN", "在线", "离线")):
            self.config._data["language"] = language
            for theme in (Theme.LIGHT, Theme.DARK):
                with self.subTest(language=language, theme=theme):
                    setTheme(theme)
                    dialog = self.make_selection_dialog()
                    dialog.searchEdit.setText("Tokyo " + online)
                    self.assertEqual(dialog.visible_ids(), [])
                    fresh = copy.deepcopy(AGENTS)
                    fresh[1]["online"] = True
                    dialog.refresh_agents(fresh, 0)
                    self.assertEqual(dialog.visible_ids(), ["b"])
                    self.assertEqual(dialog._rows["b"].status, online)
                    fresh[1] = dict(fresh[1], online=False)
                    dialog.refresh_agents(fresh, 0)
                    self.assertEqual(dialog.visible_ids(), [])
                    self.assertEqual(dialog._rows["b"].status, offline)
                    self.assertEqual(dialog.selected_ids(), ["b"])


if __name__ == "__main__":
    unittest.main()
