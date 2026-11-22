"""Workbench interaction contracts; fixtures never enter production views."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from rac_hpo.interfaces.gui.theme import apply_theme

@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    return application

def sample(identifier="EXP-contract"):
    return {"id": identifier, "algorithm": "rac_hpo", "dataset_id": "fixture", "target_unit": "cycles",
      "status": "completed", "best_trial": 0, "duration_seconds": 5,
      "config": {"model": {"name": "lightgbm"}, "optimization": {"max_trials": 10,
       "search_space": {"learning_rate": {"type": "float", "low": 0.01, "high": 1.0, "log": True}}}},
      "trials": [{"number": -1, "status": "completed", "objective": 0.01},
       {"number": 0, "status": "completed", "objective": 0.5, "params": {"learning_rate": 0.1}, "metrics": {"overall_mae": 4}},
       {"number": 1, "status": "completed", "objective": 0.8, "params": {"learning_rate": 0.5}, "metrics": {"overall_mae": 2}},
       {"number": 2, "status": "failed", "objective": None}]}

def test_rail_stays_fixed_and_has_one_tooltip(app):
    from rac_hpo.interfaces.gui.sidebar import NavigationRail, build_sidebar_navigation
    host=QWidget(); host.resize(720,560)
    rail=NavigationRail(host); build_sidebar_navigation(rail,["数据", "训练"])
    host.show(); app.processEvents()
    before=rail.geometry()
    QTest.mouseMove(rail.navigation.viewport(),rail.navigation.visualItemRect(rail.navigation.item(1)).center())
    QTest.qWait(290)
    assert rail.width()==56 and rail.geometry()==before
    assert rail.tooltip.isVisible() and rail.tooltip.text()=="训练"
    QTest.keyClick(rail.navigation,Qt.Key.Key_Escape)
    assert not rail.tooltip.isVisible()
    host.close()

def test_observation_selection_and_hidden_selection_are_independent(app):
    from rac_hpo.interfaces.gui.workspace_widgets import RecordTable
    table=RecordTable([("name","名称")]); table.enable_workbench()
    table.set_records([{"_id":"a","name":"alpha"},{"_id":"b","name":"beta"}])
    observed=[]; table.record_selected.connect(lambda r:observed.append(r["_id"]))
    table.view.selectRow(0)
    table.toggle_checked("b")
    assert observed==["a"] and table.checked_ids=={"b"}
    table.search_edit.setText("alpha")
    assert table.checked_ids=={"b"} and table.hidden_checked_count==1
    table.clear_hidden_selection()
    assert not table.checked_ids
    table.close()

def test_kpi_provenance_and_filter_scope(app):
    from rac_hpo.interfaces.gui.pages.optimize_page import OptimizePage
    page=OptimizePage(); page.show_result(sample())
    assert "#0" in page.metrics.cards["best"].caption_label.text()
    assert "#1" in page.metrics.cards["mae"].caption_label.text()
    page.trial_table.search_edit.setText('"objective": 0.5')
    assert page.distribution_chart.trial_numbers==[0]
    assert page.chart.trial_numbers==[0,1]
    page.trial_table.view.selectRow(0)
    assert not page.drawer.isVisible()
    page.trial_table.toggle_checked("EXP-contract:0")
    page.show_result(sample("EXP-other"))
    assert not page.trial_table.checked_ids
    page.close()

def test_brush_intersection_and_keyboard_clear(app):
    from rac_hpo.interfaces.gui.pages.optimize_page import OptimizePage
    page=OptimizePage(); page.show_result(sample())
    page.set_brush("learning_rate",0.01,0.2)
    assert page.trial_table.filtered_count==1
    assert page.parallel_chart.visible_numbers==[0]
    page.clear_brush()
    assert page.trial_table.filtered_count==3
    page.close()

def test_parallel_missing_values_never_become_zero(app):
    from rac_hpo.interfaces.gui.parallel_coordinates import ParallelCoordinates
    graph=ParallelCoordinates(); data=sample(); data["trials"][1]["params"]={}
    graph.set_snapshot(data)
    assert graph.axis_value(data["trials"][1],"learning_rate") is None
    assert graph.axis_value(data["trials"][2],"learning_rate")==pytest.approx(-0.3010299957)
    graph.resize(700,320); graph.show(); app.processEvents(); graph.grab(); graph.close()

def test_comparison_never_invents_cross_run_rank(app):
    from rac_hpo.interfaces.gui.pages.compare_page import ComparePage
    page=ComparePage(); a=sample(); b=sample("EXP-second"); page.show_experiments([a,b])
    page.history_table.toggle_checked(a["id"]); page.history_table.toggle_checked(b["id"])
    page.compare_selected()
    assert page.comparison.count==2
    assert "固定划分" in page.comparison.scope_label.text()
    assert "排名" in page.comparison.scope_label.text()
    page.close()


def test_nonfinite_values_display_unknown():
    from rac_hpo.interfaces.gui.widgets import format_value
    assert format_value(float('nan'))=='—'
    assert format_value(float('inf'))=='—'


def test_recorded_time_offsets_are_respected():
    from rac_hpo.interfaces.gui.workspace_state import display_time
    assert display_time('2026-10-30T01:00:00+00:00')==display_time('2026-10-30T09:00:00+08:00')
    assert '时区未记录' in display_time('2026-10-30T09:00:00')


def test_workbench_checkbox_does_not_move_observed_row(app):
    from rac_hpo.interfaces.gui.workspace_widgets import RecordTable
    table=RecordTable([('name','名称')]);table.enable_workbench()
    table.set_records([{'_id':'a','name':'alpha'},{'_id':'b','name':'beta'}])
    table.resize(400,400);table.show();app.processEvents()
    table.view.selectRow(0)
    point=table.view.visualRect(table.model.index(1,0)).center()
    QTest.mouseClick(table.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
    assert table._selected_id=='a' and table.checked_ids=={'b'}
    table.close()


def test_selection_limit_and_cross_page_preservation(app):
    from rac_hpo.interfaces.gui.workspace_widgets import RecordTable
    table=RecordTable([('n','编号')]);table.enable_workbench()
    table.set_records([{'_id':str(i),'n':i} for i in range(60)])
    for i in range(9):table.toggle_checked(str(i))
    assert len(table.checked_ids)==8
    table.set_page(1)
    assert len(table.checked_ids)==8 and table.hidden_checked_count==0
    table.search_edit.setText('missing')
    assert table.hidden_checked_count==8
    table.reset_context();assert not table.checked_ids
    table.close()


def test_maximized_parallel_retains_old_context(app):
    from rac_hpo.interfaces.gui.pages.optimize_page import OptimizePage
    page=OptimizePage();page.show_result(sample('EXP-A'));page.maximize_parallel()
    dialog=page._maximized[0]
    page.show_result(sample('EXP-B'))
    dialog.chart.brush_changed.emit('learning_rate',0.01,0.2)
    assert page.experiment_id=='EXP-B' and page.trial_table.filtered_count==3
    assert dialog.chart.visible_numbers==[0]
    page.close()
    from PySide6.QtCore import QCoreApplication,QEvent
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.processEvents()


def test_filter_and_refresh_keep_selection_scroll_and_focus(app):
    from rac_hpo.interfaces.gui.pages.optimize_page import OptimizePage
    page=OptimizePage();page.resize(1280,800);page.show();page.show_result(sample());app.processEvents();page.activateWindow();QTest.qWait(20)
    page.trial_table.toggle_checked('EXP-contract:1')
    page.trial_table.search_edit.setFocus();assert page.trial_table.search_edit.hasFocus()
    page.trial_table.search_edit.setText('completed')
    page.update_snapshot(sample())
    assert page.trial_table.checked_ids=={'EXP-contract:1'}
    assert page.trial_table.search_edit.text()=='completed'
    assert page.trial_table.search_edit.hasFocus()
    page.close()


def test_narrow_workspace_preserves_desktop_minimum_and_restores_list(app):
    from rac_hpo.interfaces.gui.pages.optimize_page import OptimizePage
    page=OptimizePage();page.resize(720,560);page.show();app.processEvents()
    assert page.width()==720
    assert page.workbench.browse_button.isVisible()
    page.workbench.browse_button.click();app.processEvents()
    assert page.workbench.list_dialog.isVisible()
    QTest.keyClick(page.workbench.list_dialog,Qt.Key.Key_Escape)
    page.resize(1440,900);app.processEvents()
    assert page.trial_table.isVisible() and not page.workbench.browse_button.isVisible()
    page.close()


def test_default_list_width_tracks_breakpoint_until_user_resizes(app):
    from rac_hpo.interfaces.gui.pages.optimize_page import OptimizePage
    page=OptimizePage();page.resize(1280,800);page.show();app.processEvents()
    page.resize(1920,1080);app.processEvents()
    assert page.workbench.splitter.sizes()[0]==320
    page.close()


def test_elapsed_scope_caption_has_visible_space(app):
    from rac_hpo.interfaces.gui.pages.optimize_page import OptimizePage
    page=OptimizePage();page.resize(1440,900);page.show();page.show_result(sample());app.processEvents()
    assert page.elapsed_caption.width()>100
    assert '总耗时' in page.elapsed_caption.text()
    page.close()


def test_curve_observation_updates_list_without_opening_details(app):
    from rac_hpo.interfaces.gui.pages.optimize_page import OptimizePage
    page=OptimizePage();page.show_result(sample())
    page.trial_table.view.selectRow(0)
    page.parallel_chart.observed.emit('EXP-contract:1')
    assert page.trial_table._selected_id=='EXP-contract:1'
    assert page.view_state.active_record_id=='EXP-contract:1'
    assert not page.drawer.isVisible()
    page.close()


def test_record_refresh_preserves_list_scroll(app):
    from rac_hpo.interfaces.gui.workspace_widgets import RecordTable
    table=RecordTable([('name','名称')]);table.enable_workbench()
    records=[{'_id':str(i),'name':'row '+str(i)} for i in range(80)]
    table.set_records(records);table.resize(400,420);table.show();app.processEvents()
    bar=table.view.verticalScrollBar();assert bar.maximum()>3
    bar.setValue(3)
    table.set_records(records+[{'_id':'new','name':'new row'}]);app.processEvents()
    assert bar.value()==3
    table.close()


def test_keyboard_brush_inputs_apply_and_clear(app):
    from rac_hpo.interfaces.gui.pages.optimize_page import OptimizePage
    page=OptimizePage();page.resize(1280,800);page.show();page.show_result(sample());app.processEvents()
    for widget,text in ((page.brush_low,'0.01'),(page.brush_high,'0.2')):
        widget.setFocus();QTest.keyClick(widget,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier);QTest.keyClicks(widget,text)
    page.apply_brush_button.setFocus();QTest.keyClick(page.apply_brush_button,Qt.Key.Key_Space)
    assert page.trial_table.filtered_count==1
    page.clear_brush_button.setFocus();QTest.keyClick(page.clear_brush_button,Qt.Key.Key_Space)
    assert page.trial_table.filtered_count==3
    page.close()


def test_tooltip_remains_on_entry_then_closes_and_first_click_navigates(app):
    from PySide6.QtCore import QEvent
    from rac_hpo.interfaces.gui.sidebar import NavigationRail,build_sidebar_navigation
    host=QWidget();host.resize(720,560);rail=NavigationRail(host)
    build_sidebar_navigation(rail,['数据','日志']);host.show();app.processEvents()
    position=rail.navigation.visualItemRect(rail.navigation.item(1)).center()
    QTest.mouseClick(rail.navigation.viewport(),Qt.MouseButton.LeftButton,pos=position)
    assert rail.navigation.currentRow()==1
    QTest.mouseMove(rail.navigation.viewport(),position);QTest.qWait(280)
    assert rail.tooltip.isVisible()
    app.sendEvent(rail.navigation.viewport(),QEvent(QEvent.Type.Leave))
    app.sendEvent(rail.tooltip,QEvent(QEvent.Type.Enter));QTest.qWait(130)
    assert rail.tooltip.isVisible()
    app.sendEvent(rail.tooltip,QEvent(QEvent.Type.Leave));QTest.qWait(130)
    assert not rail.tooltip.isVisible()
    host.close()


def test_closed_navigation_does_not_receive_other_window_events(app):
    from PySide6.QtCore import QEvent
    from rac_hpo.interfaces.gui.sidebar import NavigationRail,build_sidebar_navigation
    class ObservedRail(NavigationRail):
        def eventFilter(self,widget,event):
            if widget is getattr(self,'foreign',None) and event.type()==QEvent.Type.User:
                self.foreign_events+=1
            return super().eventFilter(widget,event)
    host=QWidget();other=QWidget();rail=ObservedRail(host)
    rail.foreign=other;rail.foreign_events=0
    build_sidebar_navigation(rail,['数据']);host.show();app.processEvents()
    rail.navigation.setFocus();app.processEvents()
    host.close();app.sendEvent(other,QEvent(QEvent.Type.User))
    assert rail.foreign_events==0
    other.close()
