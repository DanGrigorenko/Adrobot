from fastapi import FastAPI


def create_app(keitaro):
    app = FastAPI()

    @app.get('/api/campaigns')
    def campaigns(q: str = ''):
        query = q.strip().casefold()
        return [
            campaign for campaign in keitaro.campaigns()
            if not query or query in campaign['name'].casefold()
            or query == str(campaign['id'])
        ]

    return app
