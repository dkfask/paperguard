// Isolated UNO client. Documents cannot execute macros or update external links.
import java.net.StandardProtocolFamily;
import java.net.UnixDomainSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.SocketChannel;
import java.nio.file.Path;
import java.util.Arrays;
import com.sun.star.beans.PropertyValue;
import com.sun.star.bridge.XBridge;
import com.sun.star.bridge.XBridgeFactory;
import com.sun.star.comp.helper.Bootstrap;
import com.sun.star.connection.XConnection;
import com.sun.star.frame.XComponentLoader;
import com.sun.star.frame.XDesktop;
import com.sun.star.frame.XStorable;
import com.sun.star.lang.XComponent;
import com.sun.star.lib.uno.helper.WeakBase;
import com.sun.star.task.XInteractionAbort;
import com.sun.star.task.XInteractionContinuation;
import com.sun.star.task.XInteractionHandler;
import com.sun.star.task.XInteractionRequest;
import com.sun.star.text.XDocumentIndex;
import com.sun.star.text.XDocumentIndexesSupplier;
import com.sun.star.text.XTextFieldsSupplier;
import com.sun.star.uno.UnoRuntime;
import com.sun.star.uno.XComponentContext;
import com.sun.star.util.XCloseable;
import com.sun.star.util.XRefreshable;

public class PaperGuardLayout {
    // The Java 17 Unix socket avoids native JNI libraries and public TCP listeners.
    static final class PrivatePipe extends WeakBase implements XConnection {
        private final SocketChannel socket;
        private final Object reads = new Object(), writes = new Object();
        PrivatePipe(String path) throws java.io.IOException {
            socket = SocketChannel.open(StandardProtocolFamily.UNIX);
            socket.connect(UnixDomainSocketAddress.of(path));
        }
        public int read(byte[][] result, int requested) throws com.sun.star.io.IOException {
            synchronized (reads) {
                if (requested < 0 || requested > 64*1024*1024) throw new com.sun.star.io.IOException("UNO message limit", this);
                ByteBuffer buffer = ByteBuffer.allocate(requested);
                try {
                    while (buffer.hasRemaining() && socket.read(buffer) >= 0) {}
                    result[0] = Arrays.copyOf(buffer.array(),buffer.position());
                    return buffer.position();
                } catch (java.io.IOException e) { throw new com.sun.star.io.IOException("Office connection read failed",this); }
            }
        }
        public void write(byte[] bytes) throws com.sun.star.io.IOException {
            synchronized (writes) {
                try { ByteBuffer buffer=ByteBuffer.wrap(bytes); while (buffer.hasRemaining()) socket.write(buffer); }
                catch (java.io.IOException e) { throw new com.sun.star.io.IOException("Office connection write failed",this); }
            }
        }
        public void flush() {}
        public void close() throws com.sun.star.io.IOException {
            try { socket.close(); } catch (java.io.IOException e) { throw new com.sun.star.io.IOException("Office connection close failed",this); }
        }
        public String getDescription() { return "private PaperGuard job pipe"; }
    }
    static final class RejectInteractions extends WeakBase implements XInteractionHandler {
        public void handle(XInteractionRequest request) {
            for (XInteractionContinuation option:request.getContinuations()) {
                XInteractionAbort abort=UnoRuntime.queryInterface(XInteractionAbort.class,option);
                if (abort!=null) { abort.select();return; }
            }
        }
    }
    static PropertyValue property(String name,Object value) {
        PropertyValue p=new PropertyValue();p.Name=name;p.Value=value;return p;
    }
    static void store(XStorable document,Path file,String filter) throws Exception {
        document.storeToURL(file.toUri().toString(),new PropertyValue[]{property("FilterName",filter),property("Overwrite",true)});
    }
    public static void main(String[] args) {
        if (args.length!=3) { System.err.println("layout arguments invalid");System.exit(2); }
        String stage="connect";
        XComponent document=null;
        XDesktop desktop=null;
        try {
            XComponentContext local=Bootstrap.createInitialComponentContext(null);
            XBridgeFactory factory=UnoRuntime.queryInterface(XBridgeFactory.class,local.getServiceManager().createInstanceWithContext("com.sun.star.bridge.BridgeFactory",local));
            XBridge bridge=factory.createBridge("paperguard-layout","urp",new PrivatePipe(args[0]),null);
            XComponentContext remote=UnoRuntime.queryInterface(XComponentContext.class,bridge.getInstance("StarOffice.ComponentContext"));
            Object desktopObject=remote.getServiceManager().createInstanceWithContext("com.sun.star.frame.Desktop",remote);
            desktop=UnoRuntime.queryInterface(XDesktop.class,desktopObject);
            XComponentLoader loader=UnoRuntime.queryInterface(XComponentLoader.class,desktopObject);
            stage="load";
            document=loader.loadComponentFromURL(Path.of(args[1]).toUri().toString(),"_blank",0,new PropertyValue[]{
                property("Hidden",true),property("ReadOnly",true),property("MacroExecutionMode",(short)0),
                property("UpdateDocMode",(short)0),property("InteractionHandler",new RejectInteractions())});
            if (document==null) throw new IllegalStateException();
            XStorable storage=UnoRuntime.queryInterface(XStorable.class,document);
            Path directory=Path.of(args[2]);
            stage="initial_layout";
            store(storage,directory.resolve("before.pdf"),"writer_pdf_Export");
            stage="refresh";
            var indexes=UnoRuntime.queryInterface(XDocumentIndexesSupplier.class,document).getDocumentIndexes();
            for (int i=0;i<indexes.getCount();i++) UnoRuntime.queryInterface(XDocumentIndex.class,indexes.getByIndex(i)).update();
            var fields=UnoRuntime.queryInterface(XTextFieldsSupplier.class,document).getTextFields();
            XRefreshable refresh=UnoRuntime.queryInterface(XRefreshable.class,fields);
            if (refresh!=null) refresh.refresh();
            stage="export";
            store(storage,directory.resolve("after.pdf"),"writer_pdf_Export");
            store(storage,directory.resolve("updated.docx"),"Office Open XML Text");
            System.out.println("OK");
        } catch (Throwable error) {
            System.err.println("Office layout failed at "+stage+": "+error.getClass().getSimpleName());
            System.exit(3);
        } finally {
            try { if (document!=null) UnoRuntime.queryInterface(XCloseable.class,document).close(true); } catch (Throwable ignored) {}
            try { if (desktop!=null) desktop.terminate(); } catch (Throwable ignored) {}
        }
        System.exit(0);
    }
}
