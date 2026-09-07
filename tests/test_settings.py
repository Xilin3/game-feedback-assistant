from unittest.mock import patch

import pytest

from src.app import create_app
from src.providers import CompatibleProvider
from src.settings import ModelSettings, validate_base
from src.storage import Store


def test_persistence_and_key_preservation(tmp_path):
    store = Store(tmp_path / 'settings.db')
    settings = ModelSettings(store)
    result = settings.save({'api_base':'https://example.test/v1/', 'api_key':'secret-test', 'model':'model-a'})
    assert 'secret-test' not in str(result)
    assert result['has_api_key'] and result['configured']
    restored = ModelSettings(Store(store.path))
    restored.save({'api_base':'https://example.test/v1', 'api_key':'', 'model':'model-b'})
    assert restored.effective()['api_key'] == 'secret-test'
    provider = CompatibleProvider(restored.effective())
    assert provider.model == 'model-b' and provider.key == 'secret-test'
    with pytest.raises(ValueError):
        restored.save({'api_base':'https://other.test/v1','model':'model-c'})
    assert restored.effective()['model'] == 'model-b'


@pytest.mark.parametrize('base', ['http://remote.test/v1', 'https://user:pass@example.test/v1',
    'https://example.test/v1?key=secret', 'https://example.test/v1#fragment',
    'https://example.test/v1/chat/completions','https://', 'http://localhost:123@evil.test/v1'])
def test_invalid_base(base):
    with pytest.raises(ValueError):
        validate_base(base)


def test_config_api_and_analysis_snapshot(tmp_path):
    app = create_app(tmp_path / 'api.db', testing=True)
    try:
        with app.test_client() as client:
            client.get('/')
            with client.session_transaction() as session:
                headers = {'X-CSRF-Token':session['csrf']}
            values = {'api_base':'https://example.test/v1','api_key':'private-test-key','model':'custom-model'}
            assert client.post('/api/config', json=values).status_code == 403
            response = client.post('/api/config', json=values, headers=headers)
            assert response.status_code == 200
            assert 'private-test-key' not in response.get_data(as_text=True)
            assert 'private-test-key' not in client.get('/api/config').get_data(as_text=True)
            batch = client.post('/api/batches', headers=headers, json={
                'game_name':'test','source_description':'test','text':'hello'}).json
            with patch('src.app.get_provider') as factory, patch.object(app.extensions['executor'], 'submit'):
                factory.return_value.name = 'compatible'
                factory.return_value.model = 'custom-model'
                assert client.post(f"/api/batches/{batch['batch_id']}/analyze",json={},headers=headers).status_code == 202
                assert factory.call_args.args[0] == 'compatible'
                assert factory.call_args.args[1]['api_key'] == 'private-test-key'
    finally:
        app.extensions['executor'].shutdown(wait=True)
