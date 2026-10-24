from iofast_engine.rpc import _item_folder


def test_youtube_video_id_names_folder():
    name = _item_folder("https://www.youtube.com/watch?v=7T-gldvgfpo&t=30")
    assert name.startswith("7T-gldvgfpo-")


def test_each_url_gets_its_own_folder():
    a = _item_folder("https://www.youtube.com/watch?v=aaa")
    b = _item_folder("https://www.youtube.com/watch?v=bbb")
    assert a != b
    assert _item_folder("https://www.youtube.com/watch?v=aaa") == a


def test_web_page_uses_last_path_segment():
    assert _item_folder("https://example.com/blog/My Post?x=1").startswith("My-Post-")
    assert _item_folder("https://example.com/").startswith("index-")
