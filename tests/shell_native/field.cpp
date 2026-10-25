#include <QGuiApplication>
#include <QQuickView>
#include <QQuickItem>
#include <QQmlEngine>
#include <QInputMethodEvent>
#include <QMimeData>
#include <QDropEvent>
#include <QtTest/QTest>
#include <cstdio>
#include <cstdlib>
void check(bool ok, const char *name) { printf("%s %s\n", ok?"PASS":"FAIL", name); fflush(stdout); if(!ok) std::exit(1); }
int main(int argc,char**argv){
 QGuiApplication app(argc,argv);QQuickView view;
 view.engine()->addImportPath(argv[2]);view.setSource(QUrl::fromLocalFile(argv[1]));
 for(int i=0;i<100&&view.status()==QQuickView::Loading;i++)QTest::qWait(10);
 printf("source=%s status=%d argc=%d\n",view.source().toString().toUtf8().constData(),int(view.status()),argc);
 for(auto e:view.errors()) printf("QML: %s\n",e.toString().toUtf8().constData());
 check(view.status()==QQuickView::Ready,"actual EditField loads");view.show();view.requestActivate();QTest::qWait(50);
 auto root=view.rootObject();auto f=root->findChild<QQuickItem*>("field");auto other=root->findChild<QQuickItem*>("other");
 check(f&&other,"both fields retained"); f->forceActiveFocus();QTest::qWait(20);
 QTest::keyClick(&view,Qt::Key_A);QTest::keyClick(&view,Qt::Key_B);
 check(f->property("text").toString()=="ab","ordinary typing and focus");
 QTest::keyClick(&view,Qt::Key_Home);QTest::keyClick(&view,Qt::Key_Delete);QTest::keyClick(&view,Qt::Key_End);QTest::keyClick(&view,Qt::Key_Backspace);
 check(f->property("text").toString().isEmpty(),"Home End Delete Backspace");
 QInputMethodEvent pre(QString::fromUtf8("漢"),{});QCoreApplication::sendEvent(view.activeFocusItem(),&pre);
 check(f->property("text").toString().isEmpty(),"IME preedit is not committed");
 QInputMethodEvent commit;commit.setCommitString(QString::fromUtf8("é漢\nZ"));QCoreApplication::sendEvent(view.activeFocusItem(),&commit);
 check(f->property("text").toString()==QString::fromUtf8("é漢 Z"),"IME Unicode commit single-line sanitization");
 QTest::keyClick(&view,Qt::Key_A,Qt::ControlModifier);QTest::keyClick(&view,Qt::Key_X);
 check(f->property("text").toString()=="x","select all replacement");
 QTest::keyClick(&view,Qt::Key_Return);QTest::keyClick(&view,Qt::Key_Escape);
 check(root->property("returned").toInt()==1 && root->property("escaped").toInt()==1,"caller Return Escape forwarding");
 QTest::keyClick(&view,Qt::Key_V,Qt::ControlModifier);QTest::keyClick(&view,Qt::Key_Insert,Qt::ShiftModifier);QTest::keyClick(&view,Qt::Key_V,Qt::ControlModifier|Qt::ShiftModifier);
 check(root->property("pastes").toInt()==3 && f->property("text").toString()=="x","all three paste shortcuts are external-only");
 QTest::mouseClick(&view,Qt::MiddleButton,{},QPoint(20,20));QTest::mouseClick(&view,Qt::RightButton,{},QPoint(20,20));
 check(root->property("pastes").toInt()==3 && f->property("text").toString()=="x","middle and context menu routes consumed");
 QMimeData mime;mime.setText("DROP");QDropEvent drop(QPointF(20,20),Qt::CopyAction,&mime,Qt::LeftButton,{});QCoreApplication::sendEvent(&view,&drop);
 check(f->property("text").toString()=="x","drop does not insert");
 f->forceActiveFocus();QTest::keyClick(&view,Qt::Key_Tab);check(other->hasActiveFocus(),"Tab advances focus");
 QTest::keyClick(&view,Qt::Key_Backtab);check(f->hasActiveFocus(),"Backtab restores focus");
 f->setProperty("maximumLength",4);f->setProperty("text","");QInputMethodEvent longCommit;longCommit.setCommitString("123456");QCoreApplication::sendEvent(view.activeFocusItem(),&longCommit);
 check(f->property("text").toString()=="1234","IME length bound");
 return 0;
}
